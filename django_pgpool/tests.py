import sys
import time
import gevent
import pytest
import collections

from psycopg2 import OperationalError

from .psycopg2_pool import PostgresConnectionPool


dsn = "dbname=template1"


def test1():
    """
    Waiting for one slot - total 0.2s
    """

    pool = PostgresConnectionPool(dsn, maxsize=3, maxwait=1)

    start = time.time()
    for _ in range(4):
        gevent.spawn(pool.execute, 'select pg_sleep(0.1);')

    gevent.wait()
    delay = time.time() - start
    assert int(delay * 10) == 2
    pool.closeall()


def test2():
    """
    Waiting for 4 slots - total 0.3s
    """

    pool = PostgresConnectionPool(dsn, maxsize=2, maxwait=2)

    start = time.time()
    for _ in range(6):
        gevent.spawn(pool.execute, 'select pg_sleep(0.1);')

    gevent.wait()
    delay = time.time() - start
    assert int(delay * 10) == 3
    pool.closeall()


def test_overflow():
    """
    Running "select pg_sleep(0.1);" and reaching the overflow exception
    """

    def exec_sleep():
        try:
            pool.execute('select pg_sleep(0.1);')
            exec_sleep.passed += 1
        except OperationalError:
            exec_sleep.raised += 1

    exec_sleep.passed = 0
    exec_sleep.raised = 0
    pool = PostgresConnectionPool(dsn, maxsize=3, maxwait=0.15)
    for _ in range(8):
        gevent.spawn(exec_sleep)
    gevent.wait()

    assert exec_sleep.passed == 6
    assert exec_sleep.raised == 2


def test_no_expires():
    """
    """

    def exec_sleep():
        with pool.cursor() as cursor:
            cursor.execute('select pg_backend_pid();')
            pid = cursor.fetchone()[0]
            exec_sleep.conns_pids[pid] += 1
            cursor.execute('select pg_sleep(0.15);')

    exec_sleep.conns_pids = collections.defaultdict(int)

    pool = PostgresConnectionPool(dsn, maxsize=2, maxwait=0.5, expires=0.1)
    for _ in range(8):
        gevent.spawn(exec_sleep)
    gevent.wait()

    assert len(exec_sleep.conns_pids) == 2


def test_expires():
    """
    """

    def exec_sleep():
        with pool.cursor() as cursor:
            cursor.execute('select pg_backend_pid();')
            pid = cursor.fetchone()[0]
            exec_sleep.conns_pids[pid] += 1
            cursor.execute('select pg_sleep(0.15);')


    exec_sleep.conns_pids = collections.defaultdict(int)

    pool = PostgresConnectionPool(dsn, maxsize=2, maxwait=0.5, expires=0.1)

    assert pool._size == 0

    gevent.spawn(exec_sleep)
    gevent.wait()

    assert pool._size == 0
    assert len(pool._pool) == 0
    assert len(exec_sleep.conns_pids) == 1

    # Old connection should expire and new one should be created
    gevent.spawn(exec_sleep)
    gevent.wait()

    assert pool._size == 0
    assert len(pool._pool) == 0
    assert len(exec_sleep.conns_pids) == 2


def test_cleanup():
    """
    """

    def exec_sleep():
        with pool.cursor() as cursor:
            cursor.execute('select pg_backend_pid();')
            pid = cursor.fetchone()[0]
            exec_sleep.conns_pids[pid] += 1
            cursor.execute('select pg_sleep(0.15);')


    exec_sleep.conns_pids = collections.defaultdict(int)

    pool = PostgresConnectionPool(dsn, maxsize=4, maxwait=0.5, expires=2.0, cleanup=0.2)

    assert pool._size == 0

    for _ in range(4):
        gevent.spawn(exec_sleep)
    gevent.wait()

    assert pool._size == 4
    assert len(pool._pool) == 4
    assert len(exec_sleep.conns_pids) == 4

    gevent.spawn(exec_sleep)
    gevent.wait()

    # latest_use = 0, 0, 0, 0

    gevent.spawn(exec_sleep)
    assert pool._size == 4
    gevent.wait()

    # latest_use = 0, 0.15, 0.15, 0.15

    gevent.spawn(exec_sleep)
    assert pool._size == 1
    gevent.wait()

    # latest_use = 0, --> 0.30, 0.30, 0.30 <--

    gevent.spawn(exec_sleep)
    assert pool._size == 1
    gevent.wait()

    time.sleep(0.25)
    pool.cleanup()

    assert pool._size == 0


def test_overflow2():
    """
    """

    def exec_sleep():
        try:
            pool.execute('select pg_sleep(0.1);')
            exec_sleep.passed += 1
        except OperationalError:
            exec_sleep.raised += 1

    exec_sleep.passed = 0
    exec_sleep.raised = 0
    pool = PostgresConnectionPool(dsn, maxsize=3, maxwait=0.15)
    for _ in range(8):
        gevent.spawn(exec_sleep)
    gevent.wait()

    assert exec_sleep.passed == 6
    assert exec_sleep.raised == 2



if __name__ == '__main__':
    print("usage: python -m pytest -v ./tests.py")
