import datetime
import os
import re

from flask import (
    g,
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
)
from oauth2client.client import (
    # HttpAccessTokenRefreshError,
    OAuth2WebServerFlow,
    # OAuth2Credentials,
)

from app import app, sentry
from formendpoint.models import (
    db, User,
    # Post, Form, FormValidator, FormDestination, Webhook, Email, GoogleSheet,
)
from formendpoint.tasks import insert_form

login_manager = LoginManager()
login_manager.init_app(app)

PROFILE_EMBED_TEMPLATE = """<form method="POST" action="%s">
    <input type="hidden" name="_spreadsheet_url" value="YOUR GOOGLE SHEET URL">
    <input type="text" name="YOUR COLUMN NAME">

    <button type="submit"></button>
</form>"""
DEMO_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1QWeHPvZW4atIZxobdVXr3IYl8u4EnV99Dm_K4yGfo_8/'  # NOQA
DEMO_HTML = """<form method="POST" action="%s://%s/demo?destination=%s">
    <input type="email" name="email">

    <button type="submit">Submit</button>
</form>"""
GOOGLE_SHEET_URL_PATTERN = re.compile("^https\://docs\.google\.com/spreadsheets/d/(\S+)/.*")  # NOQA


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
    return render_template(
        '500.html',
        event_id=g.sentry_event_id,
        public_dsn=sentry.client.get_public_dsn('https'),
    )


@app.route('/', methods=['GET', 'POST'])
def index():
    return render_template('index.html', demo_html=DEMO_HTML % (
            app.config['PREFERRED_URL_SCHEME'],
            app.config['SERVER_NAME'],
            DEMO_SHEET_URL
        )
    )


@app.route('/login/<validation_hash>')
def login(validation_hash):
    user = User.query.filter_by(validation_hash=validation_hash).first()
    if user and user.validation_hash_added and user.validation_hash_added > \
            datetime.datetime.now() - datetime.timedelta(hours=4):
        login_user(user)
    return redirect(url_for('profile', username=user.username))


@login_required
@app.route('/auth-start')
def auth_start():
    if (current_user.is_authenticated and current_user.credentials and
            (current_user.credentials.refresh_token or
                request.args.get('force') != 'True')):
        return redirect(request.args.get('next') or
                        url_for('profile', username=current_user.username))
    return redirect(get_flow().step1_get_authorize_url())


@login_required
@app.route('/auth-finish')
def auth_finish():
    credentials = get_flow().step2_exchange(request.args.get('code'))
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
    return send_from_directory(
            os.path.join(app.root_path, 'static'),
            'favicon.ico', mimetype='image/vnd.microsoft.icon'
        )


@app.route('/<username>', methods=['GET', 'POST'])
def profile(username):
    if request.method == 'POST':
        form_data = request.form.copy()
        if '_spreadsheet_id' in form_data:
            spreadsheet_id = form_data.pop('_spreadsheet_id')
        elif '_spreadsheet_url' in form_data:
            spreadsheet_url = form_data.pop('_spreadsheet_url')
            spreadsheet_id = GOOGLE_SHEET_URL_PATTERN.search(
                spreadsheet_url).group(1)

        user = User.query.filter_by(username=username).first()
        insert_form.delay(user.id, spreadsheet_id, form_data)

        if request.args.get('next'):
            return redirect(request.args.get('next'))
        else:
            return redirect(
                    url_for('success', username=user.username, _external=True)
                )

    if current_user.is_authenticated:
        embed_form = PROFILE_EMBED_TEMPLATE % url_for(
            'profile', username=current_user.username, _external=True)
        return render_template('profile.html', embed_form=embed_form)
    return redirect(url_for('index'))


@app.route('/<username>/success')
def success(username):
    if current_user.is_authenticated:
        return "Setup your form to redirect anywhere with the next parameter."
    return "Success!"
