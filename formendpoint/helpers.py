import os

from flask import g, render_template, send_from_directory, redirect, url_for
from oauth2client.client import OAuth2WebServerFlow

from app import app, sentry
from formendpoint.models import db, Post
from formendpoint.tasks import process_post_request


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


def handle_post(request, endpoint):
    ip_address = request.remote_addr if request.remote_addr != '127.0.0.1' \
        else request.headers.get('X-Forwarded-For')

    post = Post(
        data=request.form.to_dict(),
        organization_id=endpoint.organization.id,
        endpoint_id=endpoint.id,
        referrer=request.headers.get('REFERER'),
        user_agent=request.args.get('USER-AGENT'),
        ip_address=ip_address,
    )
    db.session.add(post)
    db.session.commit()

    process_post_request.delay(post.id)

    if 'redirect' in request.args:
        return redirect(request.args.get('redirect'))
    elif endpoint.redirect:
        return redirect(endpoint.redirect)
    else:
        return redirect(url_for('success', org_name=endpoint.organization.name, _external=True))
