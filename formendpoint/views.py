from collections import defaultdict
import datetime
import json
import os
import requests
from urllib.parse import parse_qsl, urlparse

from flask import (
    abort,
    jsonify,
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

from app import app, csrf, login_manager

from formendpoint.forms import EndpointForm, LoginForm
from formendpoint.models import (
    Destination,
    Endpoint,
    EndpointDestination,
    Gmail,
    GoogleDestinationMixin,
    GoogleSheet,
    Organization,
    PersonalDestinationMixin,
    Submission,
    User,
    db,
)

DEMO_URL = 'https://docs.google.com/spreadsheets/d/1QWeHPvZW4atIZxobdVXr3IYl8u4EnV99Dm_K4yGfo_8/'
SITE_VERIFY_URL = 'https://www.google.com/recaptcha/api/siteverify'
GOOGLE_PICKER_API_KEY = os.environ['GOOGLE_PICKER_API_KEY']
GOOGLE_CLIENT_ID = os.environ['GOOGLE_CLIENT_ID']
GOOGLE_APP_ID = os.environ['GOOGLE_APP_ID']
RECAPTCHA_SECRET_KEY = os.environ['RECAPTCHA_SECRET_KEY']


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)


login_manager.login_view = "index"


@app.route('/', methods=['GET', 'POST'])
def index():
    if current_user.is_authenticated:
        if request.method == 'GET':
            endpoint = Endpoint.query.filter_by(organization=current_user.organization).first()
            if endpoint:
                return redirect(url_for('endpoint', endpoint_id=endpoint.id))
            return redirect(url_for('create_endpoint'))
        else:
            abort(405)

    else:
        url = furl(url_for('endpoint', endpoint_id=1, _external=True))
        url.args['destination'] = DEMO_URL
        demo_form = render_template('form.html', url=url.url,
                                    input='<input type="email" name="email">')
        login_form = LoginForm()

        if login_form.validate_on_submit():
            user = User.query.filter_by(email=request.form['email']).first()
            if not user:
                org = Organization()
                user = User(email=request.form['email'], organization=org)

            user.refresh_validation_hash()
            db.session.add(user)
            db.session.commit()

            user.send_confirmation_email()
            return 'Check your inbox!'

        return render_template('welcome.html', login_form=login_form,
                               demo_form=demo_form, demo_url=DEMO_URL)


@app.route('/login/<validation_hash>')
def login_with_validation(validation_hash):
    user = User.query.filter_by(validation_hash=validation_hash).first_or_404()
    if user and user.validation_hash_added and user.validation_hash_added > \
            datetime.datetime.now() - datetime.timedelta(hours=4):
        login_user(user)
    return redirect(url_for('index'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/support')
@login_required
def support():
    return render_template('support.html')

####################
# Destination Auth #
####################


@app.route('/endpoint/<endpoint_id>/destinations/<destination_type>/auth-start')
@login_required
def google_auth_start(endpoint_id, destination_type):
    """
    :param string destination: destination type
    """
    cls = [c for c in GoogleDestinationMixin.__subclasses__()
           if c.dashname == destination_type][0]
    if request.args.get('force') or not current_user.has_destination(cls):
        redirect_uri = url_for('google_auth_finish',
                               destination_type=destination_type, _external=True)
        flow = cls.get_flow(redirect_uri=redirect_uri)
        flow.params['state'] = endpoint_id
        return redirect(flow.step1_get_authorize_url())

    return redirect(request.args.get('next') or
                    url_for('create_endpoint_destination', endpoint_id=endpoint.id,
                            destination=destination_type))


@app.route('/destinations/<destination_type>/auth-finish')
@login_required
def google_auth_finish(destination_type):
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
            credentials_json=credentials.to_json(),
        )
        db.session.add(inst)

    if cls == Gmail:
        r = requests.get('https://www.googleapis.com/plus/v1/people/me',
                         headers={'Authorization': 'Bearer {}'.format(credentials.access_token)})
        inst.email = r.json()['emails'][0]['value']

    db.session.commit()
    return redirect(url_for('create_endpoint_destination',
                            endpoint_id=int(request.args.get('state')),
                            destination=destination_type))

################
# Destinations #
################


@app.route('/endpoint/<endpoint_id>/destinations')
@login_required
def destinations(endpoint_id):
    endpoint = Endpoint.query.get(endpoint_id)
    destination_classes = [c for c in Destination.__subclasses__()]

    return render_template('destinations.html', endpoint=endpoint,
                           destination_classes=destination_classes)


@app.route('/endpoint/<endpoint_id>/destination/new', methods=['GET', 'POST'])
@login_required
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


@app.route('/endpoint/<endpoint_id>/destination/<endpoint_destination_id>/delete', methods=['POST'])
@login_required
def delete_endpoint_destination(endpoint_id, endpoint_destination_id):
    ed = EndpointDestination.query.get(endpoint_destination_id)
    db.session.delete(ed)
    db.session.commit()
    return redirect(url_for('endpoint_destination', endpoint_id=endpoint_id))

##############
# Submission #
##############


@app.route('/endpoint/<endpoint_id>/submissions')
@login_required
def submissions(endpoint_id):
    endpoint = Endpoint.query.get(endpoint_id)
    return render_template('submissions.html', endpoint=endpoint)


@app.route('/<submission_id>/delete', methods=['POST'])
@login_required
def delete_submission(submission_id):
    submission = Submission.query.get(submission_id)
    endpoint_id = submission.endpoint_id
    db.session.delete(submission)
    db.session.commit()
    return redirect(url_for('endpoint', endpoint_id=endpoint_id))


@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

############
# Endpoint #
############


@app.route('/<endpoint_id>', methods=['GET', 'POST'])
@csrf.exempt
def endpoint(endpoint_id):
    """
    Data priority:
    1. Form data
    2. JSON body data
    3. request query parameters
    4. referrer query parameters

    Referrer parameters are not necessarily meaningful, but better to store in case.
    Within form or query params, you can have overlapping names to send lists, but
    between form, body, or query params, names overwrite.

    Special fields: email, next, referrer, <input name> ending in ~
    - email: used for email notifications
    - next: url for redirecting
    - referrer: overrides referrer header
    - <name>~: for error messages
    """
    if endpoint_id is not int:
        abort(404)

    endpoint = Endpoint.query.filter_by(id=endpoint_id).first()

    if request.method == 'POST':
        if endpoint:
            # Referrer
            data = defaultdict(list)
            for k, v in parse_qsl(urlparse(request.headers.get('REFERER')).query,
                                  keep_blank_values=True):
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

            ip_address = request.environ.get(
                'HTTP_X_REAL_IP',
                request.headers.get('X-Forwarded-For', request.remote_addr)
            )

            if endpoint.recaptcha:
                # TODO
                # pass referrer through
                data['referrer'] = request.args.get('referrer', request.headers.get('REFERER'))
                return render_template('recaptcha.html', form_data_json=json.dumps(data),
                                       endpoint_id=endpoint.id)

            # remove `next`
            try:
                del data['next']
            except KeyError:
                pass

            submission = Submission(
                data=dict(data),
                endpoint_id=endpoint.id,
                referrer=request.args.get('referrer', request.headers.get('REFERER')),
                user_agent=request.headers.get('USER-AGENT'),
                ip_address=ip_address,
            )
            db.session.add(submission)
            db.session.commit()

            from formendpoint.tasks import process_submission
            process_submission.delay(submission.id)

            if 'next' in request.args:
                return redirect(request.args.get('next'))
            else:
                return redirect(url_for('endpoint_success', endpoint_id=endpoint.id,
                                        _external=True))

        elif current_user.is_authenticated:
            # TODO
            return "This endpoint does not exist."
        abort(404)
    if current_user.is_authenticated:
        return render_template('endpoint.html', endpoint=endpoint)
    return redirect(url_for('index'))


@app.route('/<endpoint_id>/recaptcha', methods=["POST"])
def recaptcha(endpoint_id):
    endpoint = Endpoint.query.filter_by(id=endpoint_id).first_or_404()

    recaptcha_token = request.args.get('token')
    res = requests.post(SITE_VERIFY_URL, data={'secret': RECAPTCHA_SECRET_KEY,
                                               'response': recaptcha_token})

    if res.json()['success']:
        data = request.get_json()
        submission = Submission(
            data=dict(data),
            endpoint_id=endpoint.id,
            referrer=request.args.get('referrer', request.headers.get('REFERER')),
            user_agent=request.headers.get('USER-AGENT'),
            ip_address=request.environ.get('HTTP_X_REAL_IP',
                                           request.headers.get('X-Forwarded-For',
                                                               request.remote_addr)),
        )
        db.session.add(submission)
        db.session.commit()

        from formendpoint.tasks import process_submission
        process_submission.delay(submission.id)

        if request.args.get('next', data.get('next')):
            return jsonify(request.args.get('next', data.get('next')))
        else:
            return jsonify(url_for('endpoint_success', endpoint_id=endpoint.id))

    else:
        abort(404)


@app.route('/<endpoint_id>/success')
def endpoint_success(endpoint_id):
    # TODO
    return 'Thanks!'


@app.route('/endpoint/new', methods=['GET', 'POST'])
@login_required
def create_endpoint():
    form = EndpointForm()
    if form.validate_on_submit():
        endpoint = Endpoint(name=form.name.data, organization=current_user.organization)
        db.session.add(endpoint)
        db.session.commit()
        return redirect(url_for('endpoint', endpoint_id=endpoint.id))

    return render_template('create_endpoint.html', form=form)
