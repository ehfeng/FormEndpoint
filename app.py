# -*- coding: utf-8 -*-
import os

from celery import Celery
from flask import Flask
from flask_login import LoginManager
from raven.contrib.flask import Sentry
from raven.contrib.celery import register_signal, register_logger_signal

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SERVER_NAME'] = os.environ['SERVER_NAME']
app.config['PREFERRED_URL_SCHEME'] = os.environ['PREFERRED_URL_SCHEME']
app.config['CELERY_RESULT_BACKEND'] = os.environ['REDIS_URL']
app.config['CELERY_BROKER_URL'] = os.environ['REDIS_URL']
app.secret_key = os.environ['FLASK_SECRET_KEY']

sentry = Sentry(app)
register_logger_signal(sentry.client)
register_signal(sentry.client)


def make_celery(app):
    celery = Celery(
        app.import_name,
        backend=app.config['CELERY_RESULT_BACKEND'],
        broker=app.config['CELERY_BROKER_URL']
    )
    celery.conf.update(app.config)
    TaskBase = celery.Task

    class ContextTask(TaskBase):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)
    celery.Task = ContextTask
    return celery


celery = make_celery(app)

login_manager = LoginManager()
login_manager.init_app(app)

from formendpoint import views  # NOQA
from formendpoint import cli  # NOQA