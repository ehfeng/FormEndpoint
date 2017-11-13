# -*- coding: utf-8 -*-
import os

from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from raven.contrib.flask import Sentry
from raven.contrib.celery import register_logger_signal, register_signal


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SERVER_NAME'] = os.environ['SERVER_NAME']
app.config['PREFERRED_URL_SCHEME'] = os.environ['PREFERRED_URL_SCHEME']
app.config['CELERY_RESULT_BACKEND'] = os.environ['REDIS_URL']
app.config['CELERY_BROKER_URL'] = os.environ['REDIS_URL']
app.config['GOOGLE_PICKER_API_KEY'] = os.environ['GOOGLE_PICKER_API_KEY']
app.secret_key = os.environ['FLASK_SECRET_KEY']

sentry = Sentry(app)
register_logger_signal(sentry.client)
register_signal(sentry.client)
login_manager = LoginManager()
login_manager.init_app(app)
csrf = CSRFProtect(app)

from formendpoint import views  # NOQA
from formendpoint import cli  # NOQA
