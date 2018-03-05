#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup


setup(
    name="django_pgpool",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Framework :: Django",
        "Framework :: gevent",
        "Intended Audience :: Developers",
        "Topic :: Database :: Front-Ends",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Operating System :: POSIX",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: Implementation :: CPython"
    ],
    description="Django+gevent PostgreSQL database driver with persistent connections",
    license="MIT",
    long_description='Features: django, gevent, overflow support, expiration, waiting for slots',
    url="https://github.com/tru-software/django_pgpool",
    project_urls={
        "Documentation": "https://github.com/tru-software/django_pgpool",
        "Source Code": "https://github.com/tru-software/django_pgpool",
    },

    author="TRU SOFTWARE",
    author_email="at@tru.pl",

    setup_requires=["setuptools_scm"],
    use_scm_version=True,

    install_requires=["django", "gevent"],
    packages=["django_pgpool"]
)
