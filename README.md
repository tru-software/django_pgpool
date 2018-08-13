# django_pgpool
Django-gevent connections pool for the PostgreSQL database.

The main strategy is to keep the **smallest number** of alive connections which are required for **best service performance**.
Therefore, the priority is: **1. service availability**, **2. performance**, **3. lowest possible resources usage**.

In most cases (avarage usage) connections are taken from the pool. In case of views-peeks (high load), pool creates some
extra resources (see `maxsize` and `maxwait`) preventing service gone unavailable. In time of low traffic (night) all unnecessary connections are released up (`expires` + `cleanup`).

## Parameters
* `maxsize` : `int`
Hard limit of the number of created connections. After reaching this limit taking the next connection first waits `maxwait` time for any returned slot.
The value should be the top number of available connections for django instance.

* `maxwait` : `float`
The time in seconds which is to be wait before raising an exception after the pool gets empty. 

* `expires` : `float`
The time in seconds indicates how long connection should stay alive.
It is used to recycle connections.

* `cleanup` : `float`
The time in seconds indicates how long connection may wait for next use.
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
			'maxsize': 20,    # up to 20 connections might be created at the same time
			'maxwait': 10.0,  # after reaching 20 conns, 10.0s is a time to wait for next free slot
			'expires': 60*5,  # connection may live up to 5minutes
			'cleanup': 20     # if connection is not used for 20s it shuold be terminanted
		}
	}
}
```
The above example creates a pool which acts a follow:
* at the beginning the pool is empty
* requested connections are returned to the pool
* if in the same time `20` connections are in use and another one is requested:
  * pool waits `10.0`sec for any available connection
  * after `10.0`sec an exception is risen
  * after `20` connections no more is going to be created
* each connection may live at least `5`minutes - expired are simply closed
* on low-traffic period, if connection hasn't been used for `20s` it would be terminated
