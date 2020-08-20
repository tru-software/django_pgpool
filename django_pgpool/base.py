import logging
import sys

import django
import psycopg2.extensions

try:
    from gevent.lock import Semaphore
except ImportError:
    from eventlet.semaphore import Semaphore  # noqa

from django.db.backends.postgresql.base import DatabaseWrapper as OriginalDatabaseWrapper

from django.db.backends.signals import connection_created
from django.conf import settings
from django.utils.encoding import force_str

from . import psycopg2_pool as psypool
from .creation import DatabaseCreation


if django.VERSION <= (3, 0):
    raise ImportError("Django version 3.0.x or greater needed")


logger = logging.getLogger('django.geventpool')

connection_pools = {}
connection_pools_lock = Semaphore(value=1)


class DatabaseWrapperMixin16(object):
    def __init__(self, *args, **kwargs):
        self._pool = None
        super(DatabaseWrapperMixin16, self).__init__(*args, **kwargs)
        self.creation = DatabaseCreation(self)

    @property
    def pool(self):
        if self._pool is not None:
            return self._pool

        with connection_pools_lock:
            if self._pool is not None:
                return self._pool

            if self.alias not in connection_pools:
                self._pool = psypool.PostgresConnectionPool(**self.get_connection_params())
                connection_pools[self.alias] = self._pool
            else:
                self._pool = connection_pools[self.alias]

        return self._pool

    def get_new_connection(self, conn_params):
        if self.connection is None:
            self.connection = self.pool.get()
            self.closed_in_transaction = False
        return self.connection

    def get_connection_params(self):
        conn_params = super(DatabaseWrapperMixin16, self).get_connection_params()
        if 'MAX_CONNS' in self.settings_dict['OPTIONS']:
            conn_params['MAX_CONNS'] = self.settings_dict['OPTIONS']['MAX_CONNS']
        return conn_params

    def close(self):
        self.validate_thread_sharing()
        if self.closed_in_transaction or self.connection is None:
            return  # no need to close anything
        try:
            self._close()
        except BaseException:
            # In some cases (database restart, network connection lost etc...)
            # the connection to the database is lost without giving Django a
            # notification. If we don't set self.connection to None, the error
            # will occur at every request.
            self.connection = None
            logger.warning(
                'psycopg2 error while closing the connection.',
                exc_info=sys.exc_info())
            raise
        finally:
            self.set_clean()

    def _close(self):
        if self.connection.closed:
            self.pool.closeall()
        else:
            with self.wrap_database_errors:
                self.pool.put(self.connection)
        self.connection = None

    def closeall(self):
        for pool in list(connection_pools.values()):
            pool.closeall()


class DatabaseWrapperMixin17(object):
    def set_clean(self):
        if self.in_atomic_block:
            self.closed_in_transaction = True
            self.needs_rollback = True

    def close_too_old(self):
        self.pool.cleanup()


class DatabaseWrapper(DatabaseWrapperMixin17, DatabaseWrapperMixin16, OriginalDatabaseWrapper):
    pass
