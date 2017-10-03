# -*- coding: utf-8 -*-

import datetime
import httplib2
import os
import re
import string
import uuid

from apiclient import discovery
from celery import Celery
import click
from flask import (
    abort,
    Flask,
    g,
    json,
    redirect,
    render_template,
    request,
    send_from_directory,
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
    OAuth2WebServerFlow,
    OAuth2Credentials,
)
from raven.contrib.celery import register_signal, register_logger_signal
from raven.contrib.flask import Sentry
from sqlalchemy.dialects.postgresql import JSONB

GOOGLE_SHEETS_DISCOVERY_URL = 'https://sheets.googleapis.com/$discovery/rest?version=v4'
PROFILE_EMBED_TEMPLATE = """<form method="POST" action="%s">
    <input type="hidden" name="_spreadsheet_url" value="YOUR GOOGLE SHEET URL">
    <input type="text" name="YOUR COLUMN NAME">

    <button type="submit"></button>
</form>"""
DEMO_HTML = """<form method="POST" action="https://formendpoint.com/demo">
    <input type="hidden" name="_spreadsheet_url" value="https://docs.google.com/spreadsheets/d/1QWeHPvZW4atIZxobdVXr3IYl8u4EnV99Dm_K4yGfo_8/edit?usp=sharing">
    <input type="email" name="email">

    <button type="submit">Submit</button>
</form>"""
GOOGLE_SHEET_URL_PATTERN = re.compile("^https\://docs\.google\.com/spreadsheets/d/(\S+)/.*")

def make_celery(app):
    celery = Celery(app.import_name, backend=app.config['CELERY_RESULT_BACKEND'],
                    broker=app.config['CELERY_BROKER_URL'])
    celery.conf.update(app.config)
    TaskBase = celery.Task
    class ContextTask(TaskBase):
        abstract = True
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)
    celery.Task = ContextTask
    return celery

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SERVER_NAME'] = os.environ['SERVER_NAME']
app.config['PREFERRED_URL_SCHEME'] = os.environ['PREFERRED_URL_SCHEME']
app.config['CELERY_RESULT_BACKEND'] = os.environ['REDIS_URL']
app.config['CELERY_BROKER_URL'] = os.environ['REDIS_URL']
app.secret_key = os.environ['FLASK_SECRET_KEY']

login_manager = LoginManager()
login_manager.init_app(app)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
celery = make_celery(app)
sentry = Sentry(app)

register_logger_signal(sentry.client)
register_signal(sentry.client)

##########
# Models #
##########

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.Text, unique=True, nullable=False)
    username = db.Column(db.String, unique=True)
    verified = db.Column(db.Boolean, default=False)
    credentials_json = db.Column(JSONB)

    validation_hash = db.Column(db.Text)
    validation_hash_added = db.Column(db.DateTime)

    @property
    def credentials(self):
        if self.credentials_json:
            return OAuth2Credentials.from_json(self.credentials_json)
        else:
            return None

    @credentials.setter
    def credentials(self, cred):
        if type(cred) is OAuth2Credentials:
            self.credentials_json = cred.to_json()
        else:
            self.credentials_json = cred

    @property
    def sheets(self):
        http = self.credentials.authorize(httplib2.Http())
        return discovery.build('sheets', 'v4', http=http, discoveryServiceUrl=GOOGLE_SHEETS_DISCOVERY_URL, cache_discovery=False)

    def refresh_validation_hash(self):
        self.validation_hash = uuid.uuid4().hex
        self.validation_hash_added = datetime.datetime.now()


class GoogleSheet(object):
    @staticmethod
    def find_furthest_empty_row(data, ranges):
        return

    @staticmethod
    def convert_to_column_title(num):
        title = ''
        alist = string.ascii_uppercase
        while num:
            mod = (num-1) % 26
            num = int((num - mod) / 26)
            title += alist[mod]
        return title[::-1]

#########
# Tasks #
#########

@celery.task()
def insert_form(user_id, spreadsheet_id, form_data):
    user = User.query.get(user_id)
    spreadsheet = user.sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        includeGridData=True).execute()

    columnar_named_ranges = {
        r['name']: r['namedRangeId'] for r in spreadsheet.get('namedRanges', [])
        if r['range'].get('startRowIndex') == None
    }

    if columnar_named_ranges:
        furthest_row = GoogleSheet.find_furthest_empty_row(spreadsheet['sheets']['data'], columnar_named_ranges)
        # update_value_ranges = GoogleSheet.insert_form(row=furthest_row, ranges=columnar_named_ranges)

    else:
        first_row = next(iter(spreadsheet['sheets'][0]['data'][0].get('rowData', [])), None)
        if first_row:
            sheet_column_headers = [c.get('effectiveValue', {}).get('stringValue', None) for c in first_row['values']]
            append_row = []
            for header in sheet_column_headers:
                append_row.append(form_data.get(header, None))

            body = {
                'majorDimension': 'ROWS',
                'values': [append_row]
            }

            user.sheets.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range='A:%s' % GoogleSheet.convert_to_column_title(len(sheet_column_headers)),
                valueInputOption='RAW',
                body=body).execute()

###########
# Helpers #
###########

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)


def get_flow():
    flow = OAuth2WebServerFlow(
        client_id=os.environ['GOOGLE_CLIENT_ID'],
        client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        scope='https://www.googleapis.com/auth/spreadsheets',
        redirect_uri=url_for('auth_finish', _external=True),
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
    return render_template('index.html', demo_html=DEMO_HTML)


@app.route('/login/<validation_hash>')
def login(validation_hash):
    user = User.query.filter_by(validation_hash=validation_hash).first()
    if user and user.validation_hash_added and user.validation_hash_added > datetime.datetime.now() - datetime.timedelta(hours=4):
        login_user(user)
    return redirect(url_for('profile', username=user.username))


@login_required
@app.route('/auth-start')
def auth_start():
    if (current_user.is_authenticated and current_user.credentials and
        (current_user.credentials.refresh_token or
        request.args.get('force') != 'True')):
        return redirect(request.args.get('next') or url_for('profile', username=current_user.username))
    return redirect(get_flow().step1_get_authorize_url())


@login_required
@app.route('/auth-finish')
def auth_finish():
    credentials = get_flow().step2_exchange(request.args.get('code'))
    http = credentials.authorize(httplib2.Http())
    service = discovery.build('sheets', 'v4', http=http,
                              discoveryServiceUrl=GOOGLE_SHEETS_DISCOVERY_URL,
                              cache_discovery=False)
    current_user.credentials_json = credentials.to_json()
    db.session.add(current_user)
    db.session.commit()
    return redirect(url_for('profile', username=current_user.username))


@login_required
@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/500')
def test_error():
    assert False


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.route('/<username>', methods=['GET', 'POST'])
def profile(username):
    if request.method == 'POST':
        form_data = request.form.copy()
        spreadsheet_id = form_data.pop('_spreadsheet_id')
        if not spreadsheet_id:
            spreadsheet_url = form_data.pop('_spreadsheet_url')
            if spreadsheet_url:
                spreadsheet_id = GOOGLE_SHEET_URL_PATTERN.search(spreadsheet_url).group(1)

        user = User.query.filter_by(username=username).first()
        insert_form.delay(user.id, spreadsheet_id, form_data)

        if request.args.get('next'):
            return redirect(request.args.get('next'))
        else:
            return redirect(url_for('success', username=user.username, _external=True))

    if current_user.is_authenticated:
        embed_form = PROFILE_EMBED_TEMPLATE % url_for('profile', username=current_user.username, _external=True)
        return render_template('profile.html', embed_form=embed_form)
    return redirect(url_for('index'))


@app.route('/<username>/success')
def success(username):
    if current_user.is_authenticated:
        return "Setup your form to redirect anywhere by using the next parameter."
    return "Success!"

############
# Commands #
############

@app.cli.command()
@click.option('--email', prompt="Email")
@click.option('--username', prompt="username")
def createuser(email, username):
    db.session.add(User(email=email, username=username))
    db.session.commit()


@app.cli.command()
@click.option('--email', prompt="Email")
def login(email):
    user = User.query.filter_by(email=email).first()
    if user:
        user.refresh_validation_hash()
        db.session.add(user)
        db.session.commit()
        click.echo('Login at %s' % url_for('login', validation_hash=user.validation_hash))
    else:
        click.echo('%s doesn\'t exist. Run `flask createuser`')
