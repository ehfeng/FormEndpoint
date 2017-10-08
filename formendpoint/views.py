import datetime
import os

from flask import (
    abort,
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
from furl import furl
from oauth2client.client import (
    # HttpAccessTokenRefreshError,
    OAuth2WebServerFlow,
    # OAuth2Credentials,
)

from app import app, sentry
from formendpoint.models import (
    db,
    User,
    Post,
    Form,
    # FormValidator, FormDestination, Webhook, Email, GoogleSheet,
)
from formendpoint.tasks import process_post_request

login_manager = LoginManager()
login_manager.init_app(app)

DEMO_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1QWeHPvZW4atIZxobdVXr3IYl8u4EnV99Dm_K4yGfo_8/'  # NOQA


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


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.route('/500')
def test_error():
    assert False


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.route('/', methods=['GET', 'POST'])
def index():
    url = furl(url_for('profile', username='demo', _external=True))
    url.args['destination'] = DEMO_SHEET_URL
    form = render_template('form.html', url=url.url, input='<input type="email" name="email">')
    return render_template('index.html', form=form, demo_url=DEMO_SHEET_URL)


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
            (current_user.credentials.refresh_token or request.args.get('force') != 'True')):
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


@app.route('/<username>', methods=['GET', 'POST'])
def profile(username):
    """
    Args:
        destination: google sheet, webhook, form slug
    """
    user = User.query.filter_by(username=username).first()
    if not user:
        abort(404)

    if request.method == 'POST':
        if 'destination' in request.args:
            form = Form.get_or_create_from_destination(request.args['destination'], user)
        else:
            form = Form.get_by_origin(request.headers.get('ORIGIN'), user)
        ip_address = request.remote_addr if request.remote_addr != '127.0.0.1' \
            else request.headers.get('X-Forwarded-For')

        post = Post(
            origin=request.args.get('ORIGIN'),
            ip_address=ip_address,
            user_agent=request.args.get('USER_AGENT'),
            data=request.form.to_dict(),
            user=user,
            form=form,
        )
        db.session.add(post)
        db.session.commit()

        process_post_request.delay(post.id, request.args.to_dict())

        if form and form.redirect:
            return redirect(form.redirect)
        elif 'next' in request.args:
            return redirect(request.args.get('next'))
        else:
            return redirect(url_for('success', username=user.username, _external=True))

    form = render_template('form.html', url=url_for('profile', username=current_user.username,
                           _external=True))
    return render_template('profile.html', form=form)


@app.route('/<username>/success')
def success(username):
    if current_user.is_authenticated:
        return "Setup your form to redirect anywhere with the next parameter."
    return "Success!"
