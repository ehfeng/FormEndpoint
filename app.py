# -*- coding: utf-8 -*-

import httplib2
import os

from flask import (
    Flask, redirect,
)
from flask_login import (
    current_user,
    LoginManager,
    login_required,
    login_user,
    logout_user
)
from oauth2client.client import (
    HttpAccessTokenRefreshError,
    OAuth2WebServerFlow
)
from raven.contrib.flask import Sentry

app = Flask(__name__)
login_manager = LoginManager()
login_manager.init_app(app)
sentry = Sentry(app)

def get_flow():
    flow = OAuth2WebServerFlow(
        client_id=os.environ['GOOGLE_CLIENT_ID'],
        client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        scope='https://www.googleapis.com/auth/spreadsheets',
        redirect_uri=os.environ['GOOGLE_REDIRECT_URI'],
        )
    flow.params['access_type'] = 'offline'
    flow.params['prompt'] = 'consent'
    return flow


@app.route("/")
def hello():
    return "FormEndpoint"


@app.route('/login')
def login():
    if (current_user.is_authenticated and current_user.credentials and
        (current_user.credentials.refresh_token or
        request.args.get('force') != 'True')):
        return redirect(request.args.get('next') or url_for('home'))
    return redirect(get_flow().step1_get_authorize_url())


@app.route('/auth-finish')
def auth_finish():
    credentials = get_flow().step2_exchange(request.args.get('code'))
    http = credentials.authorize(httplib2.Http())
