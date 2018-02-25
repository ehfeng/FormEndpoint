from collections import defaultdict
import datetime
import os
from urllib.parse import parse_qsl, urlparse

from flask import (
    abort,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for
)
from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)
from furl import furl

from app import app, csrf, login_manager

from formendpoint.forms import EndpointForm
from formendpoint.models import (
    Destination,
    Endpoint,
    Gmail,
    GoogleDestinationMixin,
    GoogleSheet,
    PersonalDestinationMixin,
    Submission,
    User,
    db,
)

DEMO_URL = 'https://docs.google.com/spreadsheets/d/1QWeHPvZW4atIZxobdVXr3IYl8u4EnV99Dm_K4yGfo_8/'
GOOGLE_PICKER_API_KEY = os.environ['GOOGLE_PICKER_API_KEY']
GOOGLE_CLIENT_ID = os.environ['GOOGLE_CLIENT_ID']
GOOGLE_APP_ID = os.environ['GOOGLE_APP_ID']


def handle_submission(request, endpoint):
    """
    Data priority:
    1. Form data
    2. JSON body data
    3. request query parameters
    4. referrer query parameters

    Referrer parameters are not necessarily meaningful, but better to store in case.
    Within form or query params, you can have overlapping names to send lists, but
    between form, body, or query params, names overwrite.

    Special fields: email, referrer, next, names ending in ~
    - email: used for email notifications
    - referrer: overriding the referrer header value
    - next: url for redirecting
    - <name>~: for error messages
    """

    # Referrer
    data = defaultdict(list)
    for k, v in parse_qsl(urlparse(request.headers.get('REFERER')).query, keep_blank_values=True):
        data[k].append(v)

    # Request
    for arg in request.args:
        data[arg] = request.args.getlist(arg) or data[arg]

    # Body
    data.update(request.get_json() or {})

    # Form
    for field in request.form:
        data[field] = request.form.getlist(field) or data[field]

    # Flatten single item lists
    for k in data:
        if isinstance(data[k], list) and len(data[k]) == 1:
            data[k] = data[k][0]

    # remove `redirect`
    try:
        del data['redirect']
    except KeyError:
        pass

    ip_address = request.remote_addr if request.remote_addr != '127.0.0.1' \
        else request.headers.get('X-Forwarded-For')

    submission = Submission(
        data=dict(data),
        endpoint_id=endpoint.id,
        referrer=request.headers.get('REFERER'),
        user_agent=request.args.get('USER-AGENT'),
        ip_address=ip_address,
    )
    db.session.add(submission)
    db.session.commit()

    from formendpoint.tasks import process_submission
    process_submission.delay(submission.id)

    if 'redirect' in request.args:
        return redirect(request.args.get('redirect'))
    else:
        return redirect(url_for('endpoint', endpoint_id=endpoint.id, _external=True))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)


@app.route('/')
def index():
    if current_user.is_authenticated:
        endpoint = Endpoint.query.filter_by(organization=current_user.organization).first()
        if endpoint:
            return redirect(url_for('endpoint', endpoint_id=endpoint.id))
        return redirect(url_for('create_endpoint'))

    else:
        url = furl(url_for('organization', org_name='demo', _external=True))
        url.args['destination'] = DEMO_URL
        form = render_template('form.html', url=url.url, input='<input type="email" name="email">')
        return render_template('welcome.html', form=form, demo_url=DEMO_URL)


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico',
                               mimetype='image/vnd.microsoft.icon')


@app.route('/login/<validation_hash>')
def login(validation_hash):
    user = User.query.filter_by(validation_hash=validation_hash).first()
    if user and user.validation_hash_added and user.validation_hash_added > \
            datetime.datetime.now() - datetime.timedelta(hours=4):
        login_user(user)
    return redirect(url_for('index'))


@login_required
@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))


@login_required
@app.route('/account')
def account():
    return "User Account"

####################
# Destination Auth #
####################


@login_required
@app.route('/endpoint/<endpoint_id>/destinations/<destination_type>/auth-start')
def google_auth_start(endpoint_id, destination_type):
    """
    :param string destination: destination type
    """
    cls = [c for c in GoogleDestinationMixin.__subclasses__()
           if c.dashname == destination_type][0]
    if request.args.get('force') or not current_user.has_destination(cls):
        redirect_uri = url_for('google_auth_finish', endpoint_id=endpoint_id,
                               destination_type=destination_type, _external=True)
        return redirect(cls.get_flow(redirect_uri=redirect_uri).step1_get_authorize_url())

    return redirect(request.args.get('next') or
                    url_for('create_destination', endpoint_id=endpoint.id))


@login_required
@app.route('/endpoint/<endpoint_id>/destinations/<destination_type>/auth-finish')
def google_auth_finish(endpoint_id, destination_type):
    cls = [c for c in GoogleDestinationMixin.__subclasses__()
           if c.dashname == destination_type][0]
    redirect_uri = url_for('google_auth_finish', destination_type=destination_type, _external=True)
    credentials = cls.get_flow(redirect_uri=redirect_uri).step2_exchange(request.args.get('code'))
    inst = cls.query.filter_by(user=current_user).first()
    if inst:
        inst.credentials_json = credentials.to_json()
        db.session.add(inst)
    else:
        inst = cls(
            user_id=current_user.id,
            credentials_json=credentials.to_json()
        )
        db.session.add(inst)

    db.session.commit()
    return redirect(url_for('create_destination', endpoint_id=endpoint_id))

################
# Destinations #
################


@login_required
@app.route('/endpoint/<endpoint_id>/destinations')
def destinations(endpoint_id):
    endpoint = Endpoint.query.get(endpoint_id)
    destination_classes = [c for c in Destination.__subclasses__()]

    return render_template('destinations.html', endpoint=endpoint,
                           destination_classes=destination_classes)


@login_required
@app.route('/endpoint/<endpoint_id>/destination/new', methods=['GET', 'POST'])
def create_endpoint_destination(endpoint_id):
    """
    :param string destination: destination type
    """
    destination_type = request.args.get('destination')
    endpoint = Endpoint.query.get(endpoint_id)
    cls = Destination.dash_to_class(destination_type)

    if request.method == 'POST':
        kwargs = {'endpoint': endpoint}
        if cls == GoogleSheet:
            kwargs['spreadsheet_id'] = request.form['file']
        elif cls == Gmail:
            kwargs['sender'] = request.form['sender']
            kwargs['subject'] = request.form['subject']
            kwargs['body'] = request.form['body']

        dest = cls.query.filter_by(user_id=current_user.id).first_or_404()
        ed = dest.create_endpoint_destination(**kwargs)
        db.session.add(ed)
        db.session.commit()
        return redirect(url_for('destinations', endpoint_id=endpoint.id))

    if issubclass(cls, PersonalDestinationMixin):
        inst = cls.query.filter_by(user_id=current_user.id).first()
        if inst:
            return render_template('endpoint_destination/%s.html' % destination_type,
                                   google_picker_api_key=GOOGLE_PICKER_API_KEY,
                                   google_client_id=GOOGLE_CLIENT_ID,
                                   google_app_id=GOOGLE_APP_ID,
                                   form=inst.get_form(current_user.organization))

        return redirect(url_for('google_auth_start', endpoint_id=endpoint.id,
                                destination_type=destination_type))

    else:
        # TODO: add for non-personal destinations
        raise NotImplemented

    return redirect(url_for('endpoint', endpoint_id=endpoint_id))

############
# Endpoint #
############


@csrf.exempt
@app.route('/<endpoint_id>', methods=['GET', 'POST'])
def endpoint(endpoint_id):
    endpoint = Endpoint.query.filter_by(id=endpoint_id).first()

    if request.method == 'POST':
        if endpoint:
            return handle_submission(request, endpoint)
        elif current_user.is_authenticated:
            return "You need to create an endpoint."
        abort(404)
    return render_template('endpoint.html', endpoint=endpoint)


@login_required
@app.route('/endpoint/new', methods=['GET', 'POST'])
def create_endpoint():
    form = EndpointForm()
    if form.validate_on_submit():
        endpoint = Endpoint(name=form.name.data, organization=current_user.organization)
        db.session.add(endpoint)
        db.session.commit()
        return redirect(url_for('endpoint', endpoint_id=endpoint.id))

    return render_template('create_endpoint.html', form=form)

##############
# Submission #
##############


@login_required
@app.route('/endpoint/<endpoint_id>/submissions')
def submissions(endpoint_id):
    endpoint = Endpoint.query.get(endpoint_id)
    return render_template('submissions.html', endpoint=endpoint)


@login_required
@app.route('/<submission_id>/delete', methods=['POST'])
def delete_submission(submission_id):
    submission = Submission.query.get(submission_id)
    endpoint_id = submission.endpoint_id
    db.session.delete(submission)
    db.session.commit()
    return redirect(url_for('endpoint', endpoint_id=endpoint_id))
