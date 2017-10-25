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
from formendpoint.forms import EndpointForm, EndpointDestinationForm
from formendpoint.helpers import get_flow, handle_post
from formendpoint.models import (
    db,
    DestinationMixin,
    GoogleSheet,
    User,
    Organization,
    OrganizationMember,
    Endpoint,
)

DEMO_URL = 'https://docs.google.com/spreadsheets/d/1QWeHPvZW4atIZxobdVXr3IYl8u4EnV99Dm_K4yGfo_8/'


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
@app.route('/destinations/googlesheet/auth-start')
def google_sheets_auth_start():
    if request.args.get('force') or not current_user.has_destination(GoogleSheet):
        return redirect(get_flow().step1_get_authorize_url())

    return redirect(request.args.get('next') or
                    url_for('organization', org_name=current_user.name))


@login_required
@app.route('/destinations/googlesheet/auth-finish')
def google_sheets_auth_finish():
    credentials = get_flow().step2_exchange(request.args.get('code'))

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
@app.route('/<org_name>/<endpoint_name>/destination/<destination_name>/new')
def create_google_sheet_destination(org_name, endpoint_name, destination_name):
    try:
        cls = DestinationMixin.dash_to_class(destination_name)
    except KeyError:
        abort(404)

    if not current_user.has_destination(cls):
        return redirect('google_sheets_auth_start')

    current_user.google_sheet
    return render_template('create_google_sheet_destination.html')


@login_required
@app.route('/<org_name>/<endpoint_name>/destination/new', methods=['GET', 'POST'])
def create_destination(org_name, endpoint_name):
    org = Organization.objects.filter_by(name=org_name)
    if not current_user.is_member(org):
        abort(404)

    {c.__name__: c for c in DestinationMixin.__subclasses__()}

    form = EndpointDestinationForm(request.form)
    return render_template('create_destination.html', form=form)

#############
# Endpoints #
#############


@app.route('/<org_name>/<endpoint_name>', methods=['GET', 'POST'])
def endpoint(org_name, endpoint_name):
    org = Organization.query.filter_by(name=org_name).first_or_404()
    endpoint = Endpoint.query.filter_by(organization=org, name=endpoint_name).first_or_404()

    if request.method == 'POST':
        return handle_post(request, endpoint)

    return render_template('endpoint.html', endpoint=endpoint)


@app.route('/e/<uuid>', methods=['POST'])
def secret_endpoint(uuid):
    endpoint = Endpoint.query.filter_by(uuid=uuid).first_or_404()
    return handle_post(request, endpoint)


@login_required
@app.route('/<org_name>/endpoints/new', methods=['GET', 'POST'])
def create_endpoint(org_name):
    form = EndpointForm(request.form)
    if request.method == 'POST' and form.validate():
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
