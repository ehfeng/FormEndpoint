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
    Organization,
    Post,
    Endpoint,
    # EndpointValidator, Destination, Webhook, Email, GoogleSheet,
)
from formendpoint.tasks import process_post_request

DEMO_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1QWeHPvZW4atIZxobdVXr3IYl8u4EnV99Dm_K4yGfo_8/'  # NOQA


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)


@app.route('/', methods=['GET', 'POST'])
def index():
    url = furl(url_for('profile', org_slug='demo', _external=True))
    url.args['destination'] = DEMO_SHEET_URL
    form = render_template('form.html', url=url.url, input='<input type="email" name="email">')
    return render_template('index.html', form=form, demo_url=DEMO_SHEET_URL)


@app.route('/login/<validation_hash>')
def login(validation_hash):
    user = User.query.filter_by(validation_hash=validation_hash).first()
    if user and user.validation_hash_added and user.validation_hash_added > \
            datetime.datetime.now() - datetime.timedelta(hours=4):
        login_user(user)
    return redirect(url_for('profile', slug=user.slug))


@login_required
@app.route('/auth-start')
def auth_start():
    if (current_user.is_authenticated and current_user.credentials and
            (current_user.credentials.refresh_token or request.args.get('force') != 'True')):
        return redirect(request.args.get('next') or
                        url_for('profile', slug=current_user.slug))
    return redirect(get_flow().step1_get_authorize_url())


@login_required
@app.route('/auth-finish')
def auth_finish():
    credentials = get_flow().step2_exchange(request.args.get('code'))
    current_user.credentials_json = credentials.to_json()
    db.session.add(current_user)
    db.session.commit()
    return redirect(url_for('profile', slug=current_user.slug))


@login_required
@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/<org_slug>/<endpoint_slug>', methods=['GET', 'POST'])
def endpoint(org_slug, endpoint_slug):
    org = Organization.query.filter_by(slug=org_slug).first()
    if not org:
        abort(404)

    endpoint = Endpoint.query.filter_by(organization=org, slug=endpoint_slug).first()
    if not endpoint:
        abort(404)

    if request.method == 'POST':
        ip_address = request.remote_addr if request.remote_addr != '127.0.0.1' \
            else request.headers.get('X-Forwarded-For')

        post = Post(
            data=request.form.to_dict(),
            organization=org,
            endpoint=endpoint,
            referrer=request.args.get('REFERER'),
            user_agent=request.args.get('USER-AGENT'),
            ip_address=ip_address,
        )
        db.session.add(post)
        db.session.commit()
        return redirect(url_for('index'))
    return 'Endpoint return'


@app.route('/<org_slug>', methods=['GET', 'POST'])
def profile(org_slug):
    """
    Args:
        destination: google sheet, webhook, form slug
    """
    org = Organization.query.filter_by(slug=org_slug).first()
    if not org:
        abort(404)

    if request.method == 'POST':
        ip_address = request.remote_addr if request.remote_addr != '127.0.0.1' \
            else request.headers.get('X-Forwarded-For')

        post = Post(
            data=request.form.to_dict(),
            organization=org,
            referrer=request.args.get('REFERER'),
            user_agent=request.args.get('USER-AGENT'),
            ip_address=ip_address,
        )
        db.session.add(post)
        db.session.commit()

        process_post_request.delay(post.id)

        if endpoint and endpoint.redirect:
            return redirect(endpoint.redirect)
        elif 'next' in request.args:
            return redirect(request.args.get('next'))
        else:
            return redirect(url_for('success', org_slug=org.slug, _external=True))

    form = render_template('form.html', url=url_for('profile', org_slug=org.slug,
                           _external=True))
    return render_template('profile.html', form=form)


@app.route('/<org_slug>/success')
def success(org_slug):
    if current_user.is_authenticated:
        return "Setup your form to redirect anywhere with the next parameter."
    return "Success!"
