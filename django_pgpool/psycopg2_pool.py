
# pylint:disable=import-error,broad-except,bare-except
import sys
import time
import contextlib

import gevent
from gevent.queue import Queue, Empty
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

    def __init__(self, maxsize=100, maxwait=1.0, maxoverflow=120, expires=None):
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
        maxoverflow : int
                      Hard limit of the created connections. After reaching this limit taking the next
                      connection results an exception to be raised - `psycopg2.OperationalError`.
        maxwait : float
                  The time in seconds which is to be wait before creating new connection after the pool gets empty.
                  It may be 0 then immediate connections are created til `maxoverflow` is reached.
        expires : float
                  The time in seconds indicates how long connection should stay alive.
                  It is also used to close unneeded slots.
        """
        if not isinstance(maxsize, integer_types):
            raise TypeError('Expected integer, got %r' % (maxsize, ))
        if not isinstance(maxoverflow, integer_types):
            raise TypeError('Expected integer, got %r' % (maxoverflow, ))
        self.maxsize = maxsize
        self.maxwait = maxwait
        self.maxoverflow = maxoverflow
        self.expires = expires
        self.created_at = {}
        self.pool = Queue()
        self.size = 0

    def create_connection(self):
        raise NotImplementedError()

    def get(self):
        pool = self.pool
        if self.size >= self.maxsize or pool.qsize():
            limit = time.time() - self.expires if self.expires else None
            try:
                 block = True
                 while self.size > 0:
                    item = pool.get(block=block, timeout=self.maxwait)
                    if limit is not None and self.created_at.get(id(item), 0) < limit:
                        block = False
                        try:
                            self.size = max(self.size-1, 0)
                            self.created_at.pop(id(item), None)
                            item.close()
                            item = None
                        except Exception:
                            pass
                        continue
                    return item
            except Empty:
                pass

        try:
            # Creation takes some time, therefore due to the concurrent connections needs,
            # the counter "size" has to be incremented in the first place.
            self.size += 1

            if self.size > self.maxoverflow:
                raise OperationalError("Too many connections created: {} (maxoverflow is {})".format(self.size, self.maxoverflow))

            conn = self.create_connection()
            self.created_at[id(conn)] = time.time()
            return conn
        except:
            self.size -= 1
            raise

    def put(self, item):
        if self.pool.qsize() >= self.maxsize:
            try:
                item.close()
            except Exception:
                pass
            self.size = max(self.size-1, 0)
        else:
            self.pool.put(item)

    def closeall(self):
        while not self.pool.empty():
            conn = self.pool.get_nowait()
            try:
                conn.close()
            except Exception:
                pass
        self.size = 0

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
        pool_kwargs = {i: kwargs.pop(i) for i in ('maxsize', 'maxwait', 'maxoverflow', 'expires') if i in kwargs}
        self.args = args
        self.kwargs = kwargs
        AbstractDatabaseConnectionPool.__init__(self, **pool_kwargs)

    def create_connection(self):
        return self.connect(*self.args, **self.kwargs)


def main():
    dsn = "dbname=template1 user=postgres"
    import time
    pool = PostgresConnectionPool(dsn, maxsize=3, maxoverflow=3, maxwait=10)
    print('1. Running "select pg_sleep(1);" 4 times with 3 connections. Should take about 2 seconds...')
    start = time.time()
    for _ in range(4):
        gevent.spawn(pool.execute, 'select pg_sleep(1);')
    gevent.wait()
    delay = time.time() - start
    print('Got %.2fs'%delay)
    pool.closeall()

    print('2. Running "select pg_sleep(1);" and reaching the overflow exception')
    def exec_sleep():
        try:
            pool.execute('select pg_sleep(1);')
            exec_sleep.passed += 1
        except OperationalError:
            exec_sleep.raised += 1

    exec_sleep.passed = 0
    exec_sleep.raised = 0
    pool = PostgresConnectionPool(dsn, maxsize=3, maxoverflow=5, maxwait=0.01)
    idx = 0
    for idx in range(8):
        gevent.spawn(exec_sleep)
    gevent.wait()
    print('Passed %d (should be 5), failed %d (should be 3)' % (exec_sleep.passed, exec_sleep.raised))


if __name__ == '__main__':
    main()
