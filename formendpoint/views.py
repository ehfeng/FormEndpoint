import datetime
import os

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

from app import app, csrf, login_manager

from formendpoint.forms import EndpointForm
from formendpoint.helpers import handle_post
from formendpoint.models import (
    DestinationMixin,
    Endpoint,
    GooglePersonalDestination,
    GoogleSheet,
    Organization,
    OrganizationMember,
    PersonalDestinationMixin,
    User,
    db,
)

DEMO_URL = 'https://docs.google.com/spreadsheets/d/1QWeHPvZW4atIZxobdVXr3IYl8u4EnV99Dm_K4yGfo_8/'
GOOGLE_PICKER_API_KEY = os.environ['GOOGLE_PICKER_API_KEY']
GOOGLE_CLIENT_ID = os.environ['GOOGLE_CLIENT_ID']
GOOGLE_APP_ID = os.environ['GOOGLE_APP_ID']


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)


@app.route('/')
def index():
    url = furl(url_for('organization', org_name='demo', _external=True))
    url.args['destination'] = DEMO_URL
    form = render_template('form.html', url=url.url, input='<input type="email" name="email">')
    return render_template('index.html', form=form, demo_url=DEMO_URL)


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

####################
# Destination Auth #
####################


@login_required
@app.route('/destinations/<destination_type>/auth-start')
def google_auth_start(destination_type):
    cls = [c for c in GooglePersonalDestination.__subclasses__()
           if c.dashname == destination_type][0]
    if request.args.get('force') or not current_user.has_destination(GoogleSheet):
        redirect_uri = url_for('google_auth_finish', destination_type=destination_type,
                               _external=True)
        return redirect(cls.get_flow(redirect_uri=redirect_uri).step1_get_authorize_url())

    return redirect(request.args.get('next') or
                    url_for('organization', org_name=current_user.personal_organization.name))


@login_required
@app.route('/destinations/<destination_type>/auth-finish')
def google_auth_finish(destination_type):
    cls = [c for c in GooglePersonalDestination.__subclasses__()
           if c.dashname == destination_type][0]
    redirect_uri = url_for('google_auth_finish', destination_type=destination_type, _external=True)
    credentials = cls.get_flow(redirect_uri=redirect_uri).step2_exchange(request.args.get('code'))

    if current_user.google_sheet:
        current_user.google_sheet.credentials_json = credentials.to_json()
        db.session.add(current_user.google_sheet)
    else:
        gs = GoogleSheet(
            user_id=current_user.id,
            credentials_json=credentials.to_json()
        )
        db.session.add(gs)

    db.session.commit()
    return redirect(url_for('index'))

#########################
# Endpoint Destinations #
#########################


@login_required
@app.route('/<org_name>/<endpoint_name>/destinations/<destination_type>/new',
           methods=['GET', 'POST'])
def create_endpoint_destination(org_name, endpoint_name, destination_type):
    org = Organization.query.filter_by(name=org_name).first_or_404()
    endpoint = Endpoint.query.filter_by(organization=org, name=endpoint_name).first_or_404()

    try:
        cls = DestinationMixin.dash_to_class(destination_type)
    except KeyError:
        abort(404)

    if request.method == 'POST':
        kwargs = {'endpoint': endpoint}
        if cls == GoogleSheet:
            kwargs['google_file_id'] = request.form['google_file_id']

        dest = cls.query.filter_by(user_id=current_user.id).first_or_404()
        ed = dest.create_endpoint_destination(**kwargs)
        db.session.add(ed)
        db.session.commit()
        return redirect(url_for('endpoint', org_name=org_name, endpoint_name=endpoint_name))

    if issubclass(cls, PersonalDestinationMixin):
        inst = cls.query.filter_by(user_id=current_user.id).first()
        if inst:
            return render_template('create_endpoint_destination.html',
                                   google_picker_api_key=GOOGLE_PICKER_API_KEY,
                                   google_client_id=GOOGLE_CLIENT_ID,
                                   google_app_id=GOOGLE_APP_ID,
                                   form=inst.form)

        return redirect(url_for('google_auth_start', destination_type=destination_type))

    else:
        # TODO: add for non-personal destinations
        raise NotImplemented

    return redirect(url_for('endpoint', org_name=org_name, endpoint_name=endpoint_name))

#############
# Endpoints #
#############


@csrf.exempt
@app.route('/<org_name>/<endpoint_name>', methods=['GET', 'POST'])
def endpoint(org_name, endpoint_name):
    org = Organization.query.filter_by(name=org_name).first_or_404()
    endpoint = Endpoint.query.filter_by(organization=org, name=endpoint_name).first_or_404()

    if request.args.get('destination') in [d.dashname for d in DestinationMixin.__subclasses__()]:
        return redirect(url_for('create_endpoint_destination', org_name=org_name,
                                endpoint_name=endpoint_name,
                                destination_type=request.args.get('destination')))

    if request.method == 'POST':
        return handle_post(request, endpoint)

    return render_template('endpoint.html', endpoint=endpoint,
                           destination_types={c.dashname: c.human_name
                                              for c in DestinationMixin.__subclasses__()})


@csrf.exempt
@app.route('/e/<uuid>', methods=['POST'])
def secret_endpoint(uuid):
    endpoint = Endpoint.query.filter_by(uuid=uuid).first_or_404()
    return handle_post(request, endpoint)


@login_required
@app.route('/<org_name>/endpoints/new', methods=['GET', 'POST'])
def create_endpoint(org_name):
    form = EndpointForm(request.form)
    if form.validate_on_submit():
        org = Organization.query.filter(
            (Organization.name == org_name) &
            Organization.id.in_(
                db.session.query(OrganizationMember.organization_id).filter_by(
                    user_id=current_user.id
                )
            )
        ).first_or_404()
        e = Endpoint(secret=form.secret.data, name=form.name.data, organization_id=org.id)
        db.session.add(e)
        db.session.commit()

        return redirect(url_for('organization', org_name=org.name))

    return render_template('create_endpoint.html', form=form)


@app.route('/<org_name>', methods=['GET', 'POST'])
def organization(org_name):
    """
    Args:
        destination: google sheet, webhook, form name
    """
    org = Organization.query.filter_by(name=org_name).first_or_404()
    form = render_template('form.html', url=url_for('organization', org_name=org.name,
                           _external=True))
    return render_template('organization.html', org=org, form=form)


@app.route('/<org_name>/success')
def success(org_name):
    if current_user.is_authenticated:
        return "Setup your form to redirect anywhere with the next parameter."
    return "Success!"
