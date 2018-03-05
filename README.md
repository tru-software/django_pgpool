# django_pgpool
Django-gevent connections pool for the PostgreSQL database.

The main strategy is to keep the **smallest number** of alive connections which are required for **best service performance**.
Therefore, the priority is: **1. service availability**, **2. performance**, **3. lowest possible resources usage**.

In most cases (avarage usage) connections are taken from the pool. In case of views-peeks (high load), pool creates some
extra resources (see `maxoverflow`) preventing service gone unavailable. In time of low traffic (night) all unnecessary
connections are released up to latest one connection (`expires`).

## Parameters
* `maxsize` : `int`
Soft limit of the number of created connections. After reaching this limit taking the next connection first waits `maxwait` time for any returned slot.
The value should be the top number of required connections used by django instance at the same time. 

* `maxoverflow` : `int`
Hard limit of the created connections. After reaching this limit taking the next
connection results an exception - `psycopg2.OperationalError`.

* `maxwait` : `float`
The time in seconds which is to be wait before creating new connection after the pool gets empty.
It may be `0` then immediate connections are created til `maxoverflow` is reached.

* `expires` : `float`
The time in seconds indicates how long connection should stay alive.
It is used to close unneeded slots.


## Requirements
* Python2 or Python3
* [gevent](http://www.gevent.org/)
* [Django 1.5 - 2.0](https://docs.djangoproject.com/)
* [PostgreSQL](https://www.postgresql.org/)

## Installation
`pip install -e "git+https://github.com/tru-software/django_pgpool#egg=django_pgpool"`

## Setup
For complete setup see [django docs](https://docs.djangoproject.com/en/2.0/ref/settings/#databases).

In your `settings.py` change `ENGINE` from `django.db.backends.postgresql` to `django_pgpool`
and add some more parameters (`OPTIONS`, `CONN_MAX_AGE`, `ATOMIC_REQUESTS`) as follow:
```python
DATABASES = {
	'default': {
		'ENGINE': 'django_pgpool',
		'NAME': 'your_database_name',
		'USER': 'your_database_user',
		'PASSWORD': 'your_database_password',
		'HOST': '127.0.0.1',
		'PORT': 5432,
		'ATOMIC_REQUESTS': False,
		'CONN_MAX_AGE': 0,
		'OPTIONS': {
			'maxsize': 2,
			'maxwait': 0.2,
			'maxoverflow': 20,
			'expires': 10 * 60
		}
	}
}
```
The above example creates a pool which acts a follow:
* at the beginning the pool is empty
* requested connections are returned to the pool, which is the size of `2`
* if in the same time `2` connections are in use and another one is requested:
  * pool waits `0.2`sec for any available connection then creates new one
  * connections are created til number of all reaches the limit `20`, then exception is risen
  * first 2 released connections are returned to the pool
  * third (extra connection) after release is closed immediately
* each connection may live only `10`minutes - requesting new connection closes expired ones
* requesting only one connection at time, after `10`minutes, the pool will remains with only one connection
