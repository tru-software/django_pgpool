
# pylint:disable=import-error,broad-except,bare-except
import sys
import time
import contextlib

import gevent
from gevent.queue import LifoQueue, Empty
from gevent.socket import wait_read, wait_write
from psycopg2 import extensions, OperationalError, connect

try:
    from gevent.lock import Semaphore
except ImportError:
    from eventlet.semaphore import Semaphore


if sys.version_info[0] >= 3:
    integer_types = (int,)
else:
    import builtins
    integer_types = (int, builtins.long)  # noqa


def gevent_wait_callback(conn, timeout=None):
    """A wait callback useful to allow gevent to work with Psycopg."""
    while True:
        state = conn.poll()
        if state == extensions.POLL_OK:
            break
        elif state == extensions.POLL_READ:
            wait_read(conn.fileno(), timeout=timeout)
        elif state == extensions.POLL_WRITE:
            wait_write(conn.fileno(), timeout=timeout)
        else:
            raise OperationalError(
                "Bad result from poll: %r" % state)


extensions.set_wait_callback(gevent_wait_callback)


class AbstractDatabaseConnectionPool(object):

    def __init__(self, maxsize=100, maxwait=1.0, expires=None, cleanup=None):
        """
        The pool manages opened connections to the database. The main strategy is to keep the smallest number
        of alive connections which are required for best web service performance.
        In most cases connections are taken from the pool. In case of views-peeks, pool creates some
        extra resources preventing service gone unavailable. In time of low traffic (night) unnecessary
        connections are released.

        Parameters
        ----------
        maxsize : int
                  Soft limit of the number of created connections. After reaching this limit
                  taking the next connection first waits `maxwait` time for any returned slot.
        maxwait : float
                  The time in seconds which is to be wait before creating new connection after the pool gets empty.
                  It may be 0 then immediate connections are created til `maxoverflow` is reached.
        expires : float
                  The time in seconds indicates how long connection should stay alive.
                  It is also used to close unneeded slots.
        """
        if not isinstance(maxsize, integer_types):
            raise TypeError('Expected integer, got %r' % (maxsize, ))

        self._maxsize = maxsize
        self._maxwait = maxwait
        self._expires = expires
        self._cleanup = cleanup
        self._created_at = {}
        self._latest_use = {}
        self._pool = LifoQueue()
        self._size = 0
        self._latest_cleanup = 0 if self._expires or self._cleanup else 0xffffffffffffffff
        self._interval_cleanup = min(self._expires or self._cleanup, self._cleanup or self._expires) if self._expires or self._cleanup else 0
        self._cleanup_lock = Semaphore(value=1)

    def create_connection(self):
        raise NotImplementedError()

    def close_connection(self, item):
        try:
            self._size -= 1
            self._created_at.pop(id(item), None)
            self._latest_use.pop(id(item), None)
            item.close()
        except Exception:
            pass

    def cleanup(self):
        self._cleanup_queue(time.time())

    def _cleanup_queue(self, now):

        if self._latest_cleanup > now:
            return

        with self._cleanup_lock:

            if self._latest_cleanup > now:
                 return

            self._latest_cleanup = now + self._interval_cleanup

            cleanup = now - self._cleanup if self._cleanup else None
            expires = now - self._expires if self._expires else None

            old_pool, self._pool = self._pool, LifoQueue()

            try:
                # try to fill self._pool ASAP, preventing creation of new connections.
                # because of LIFO there will be reversed order after this loop
                while not old_pool.empty():
                    item = old_pool.get_nowait()
                    if cleanup and self._latest_use.get(id(item), 0) < cleanup:
                        self.close_connection(item)
                    elif expires and self._created_at.get(id(item), 0) < expires:
                        self.close_connection(item)
                    else:
                        self._pool.put_nowait(item)
            except Empty:
                pass

            if self._pool.qsize() < 2:
                return

            # Reverse order back (fresh connections shuold be at the front)
            old_pool, self._pool = self._pool, LifoQueue()
            try:
                while not old_pool.empty():
                    self._pool.put_nowait(old_pool.get_nowait())
            except Empty:
                pass

    def get(self):

        try:
            return self._pool.get_nowait()
        except Empty:
            pass

        if self._size < self._maxsize:
            try:
                self._size += 1
                conn = self.create_connection()
            except:
                self._size -= 1
                raise

            now = time.time()
            self._created_at[id(conn)] = now
            self._latest_use[id(conn)] = now
            return conn
        else:
            try:
                print(f"All used {self._size} of {self._maxsize} - waiting")
                item = self._pool.get(timeout=self._maxwait)
                print("got connection")
                return item
            except Empty:
                raise OperationalError("Too many connections created: {} (maxsize is {})".format(self._size, self._maxsize))


    def put(self, conn):
        now = time.time()
        self._pool.put_nowait(conn)
        self._latest_use[id(conn)] = now

        self._cleanup_queue(now)

    def closeall(self):
        while not self._pool.empty():
            conn = self._pool.get_nowait()
            try:
                conn.close()
            except Exception:
                pass
        self._size = 0

    @contextlib.contextmanager
    def connection(self, isolation_level=None):
        conn = self.get()
        try:
            if isolation_level is not None:
                if conn.isolation_level == isolation_level:
                    isolation_level = None
                else:
                    conn.set_isolation_level(isolation_level)
            yield conn
        except:
            if conn.closed:
                conn = None
                self.closeall()
            else:
                conn = self._rollback(conn)
            raise
        else:
            if conn.closed:
                raise OperationalError("Cannot commit because connection was closed: %r" % (conn, ))
            conn.commit()
        finally:
            if conn is not None and not conn.closed:
                if isolation_level is not None:
                    conn.set_isolation_level(isolation_level)
                self.put(conn)

    @contextlib.contextmanager
    def cursor(self, *args, **kwargs):
        isolation_level = kwargs.pop('isolation_level', None)
        with self.connection(isolation_level) as conn:
            yield conn.cursor(*args, **kwargs)

    def _rollback(self, conn):
        try:
            conn.rollback()
        except:
            gevent.get_hub().handle_error(conn, *sys.exc_info())
            return
        return conn

    def execute(self, *args, **kwargs):
        with self.cursor(**kwargs) as cursor:
            cursor.execute(*args)
            return cursor.rowcount

    def fetchone(self, *args, **kwargs):
        with self.cursor(**kwargs) as cursor:
            cursor.execute(*args)
            return cursor.fetchone()

    def fetchall(self, *args, **kwargs):
        with self.cursor(**kwargs) as cursor:
            cursor.execute(*args)
            return cursor.fetchall()

    def fetchiter(self, *args, **kwargs):
        with self.cursor(**kwargs) as cursor:
            cursor.execute(*args)
            while True:
                items = cursor.fetchmany()
                if not items:
                    break
                for item in items:
                    yield item


class PostgresConnectionPool(AbstractDatabaseConnectionPool):

    def __init__(self, *args, **kwargs):
        self.connect = kwargs.pop('connect', connect)
        pool_kwargs = {i: kwargs.pop(i) for i in ('maxsize', 'maxwait', 'expires', 'cleanup') if i in kwargs}
        self.args = args
        self.kwargs = kwargs
        AbstractDatabaseConnectionPool.__init__(self, **pool_kwargs)

    def create_connection(self):
        return self.connect(*self.args, **self.kwargs)


def main():
    pass

if __name__ == '__main__':
    main()
