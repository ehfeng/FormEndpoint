# -*- coding: utf-8 -*-

import httplib2
import os

from apiclient import discovery
import click
from flask import (
    abort,
    Flask,
    g,
    json,
    redirect,
    render_template,
    request,
    url_for
)
from flask_login import (
    current_user,
    LoginManager,
    login_required,
    login_user,
    logout_user,
    UserMixin,
)
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from oauth2client.client import (
    HttpAccessTokenRefreshError,
    OAuth2WebServerFlow
)
from raven.contrib.flask import Sentry

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

login_manager = LoginManager()
login_manager.init_app(app)
sentry = Sentry(app)
db = SQLAlchemy(app)
migrate = Migrate(app, db)

##########
# Models #
##########

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.Text, unique=True, nullable=False)

############
# Commands #
############

@app.cli.command()
@click.option('--email', prompt="Email")
def createuser(email):
    click.echo(email)
    db.session.add(User(email=email))
    db.session.commit()

###########
# Helpers #
###########

def get_flow():
    flow = OAuth2WebServerFlow(
        client_id=os.environ['GOOGLE_CLIENT_ID'],
        client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        scope='email',
        redirect_uri=os.environ['GOOGLE_REDIRECT_URI'],
        )
    flow.params['access_type'] = 'offline'
    flow.params['prompt'] = 'consent'
    return flow


@app.errorhandler(500)
def internal_server_error(error):
    return render_template('500.html',
        event_id=g.sentry_event_id,
        public_dsn=sentry.client.get_public_dsn('https')
    )

##########
# Routes #
##########

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/auth-start')
def auth_start():
    if (current_user.is_authenticated and current_user.credentials and
        (current_user.credentials.refresh_token or
        request.args.get('force') != 'True')):
        return redirect(request.args.get('next') or url_for('home'))
    return redirect(get_flow().step1_get_authorize_url())


@app.route('/auth-finish')
def auth_finish():
    credentials = get_flow().step2_exchange(request.args.get('code'))
    http = credentials.authorize(httplib2.Http())
    discoveryUrl = ('https://sheets.googleapis.com/$discovery/rest?'
                    'version=v4')
    service = discovery.build('sheets', 'v4', http=http,
                              discoveryServiceUrl=discoveryUrl)
    # service = discovery.


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/500')
def test_error():
    assert False
