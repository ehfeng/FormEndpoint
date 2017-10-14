import datetime

from flask import (
    abort,
    redirect,
    render_template,
    request,
    url_for
)
from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)
from furl import furl

from app import app, login_manager
from formendpoint.helpers import get_flow
from formendpoint.models import (
    db,
    User,
    Post,
    Form,
    # FormValidator, FormDestination, Webhook, Email, GoogleSheet,
)
from formendpoint.tasks import process_post_request

DEMO_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1QWeHPvZW4atIZxobdVXr3IYl8u4EnV99Dm_K4yGfo_8/'  # NOQA


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)


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
        destinations = request.args.getlist('destination')
        if destinations:
            form = Form.get_or_create_from_destination(request.args['destination'], user)
        else:
            form = Form.get_by_referrer(request.headers.get('REFERER'), user)

        ip_address = request.remote_addr if request.remote_addr != '127.0.0.1' \
            else request.headers.get('X-Forwarded-For')

        post = Post(
            referrer=request.args.get('REFERER'),
            ip_address=ip_address,
            user_agent=request.args.get('USER-AGENT'),
            data=request.form.to_dict(),
            user=user,
            form=form,
        )
        db.session.add(post)
        db.session.commit()

        process_post_request.delay(post.id)

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
