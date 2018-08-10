
# pylint:disable=import-error,broad-except,bare-except
import sys
import time
import contextlib

import gevent
from gevent.queue import LifoQueue, Empty
from gevent.socket import wait_read, wait_write
from psycopg2 import extensions, OperationalError, connect


if sys.version_info[0] >= 3:
    integer_types = (int,)
else:
    import builtins
    integer_types = (int, builtins.long)


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

        now = time.time()

        cleanup = now - self._cleanup if self._cleanup else None
        expires = now - self._expires if self._expires else None

        pool = self._pool

        conns = []

        try:
            for i in range(pool.qsize()):
                item = pool.get_nowait()
                if cleanup and self._latest_use.get(id(item), 0) < cleanup:
                    self.close_connection(item)
                elif expires and self._created_at.get(id(item), 0) < expires:
                    self.close_connection(item)
                else:
                    conns.append(item)
        except Empty:
            pass

        for i in reversed(conns):
            pool.put(i)

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
                item = self._pool.get(timeout=self._maxwait)
                return item
            except Empty:
                raise OperationalError("Too many connections created: {} (maxsize is {})".format(self._size, self._maxsize))


    def put(self, conn):
        self._latest_use[id(conn)] = time.time()
        self._pool.put_nowait(conn)

        now = time.time()
        if self._latest_cleanup < now:
            self.cleanup()
            self._latest_cleanup = now + self._interval_cleanup

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
