import base64
from collections import UserList
from copy import deepcopy
import datetime
from email.message import EmailMessage
from email.mime.text import MIMEText
import httplib2
import inflection
import os
import re
import smtplib
import string
import uuid

from apiclient import discovery
from flask import abort, url_for
from flask_login import UserMixin
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from jinja2 import Environment, Template
from oauth2client.client import OAuth2Credentials, OAuth2WebServerFlow
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import validates
from sqlalchemy.sql import func

from app import app
from formendpoint.forms import GmailForm, GoogleSheetForm

GOOGLE_SHEETS_DISCOVERY_URL = 'https://sheets.googleapis.com/$discovery/rest?version=v4'
GOOGLE_DRIVE_DISCOVERY_URL = 'https://drive.googleapis.com/$discovery/rest?version=v2'
GOOGLE_SHEET_URL_PATTERN = re.compile("^https\://docs\.google\.com/spreadsheets/d/(\S+)/.*")


class defaultlist(UserList):
    def __setitem__(self, i, v):
        if i >= len(self.data):
            self.data += [None] * (i - len(self.data))
            self.data.append(v)
        else:
            self.data[i] = v


def sane_repr(*attrs):
    if 'id' not in attrs:
        attrs = ('id', ) + attrs

    def _repr(self):
        cls = type(self).__name__
        pairs = ('%s=%s' % (a, repr(getattr(self, a, None))) for a in attrs)
        return u'<%s at 0x%x: %s>' % (cls, id(self), ', '.join(pairs))

    return _repr


class classproperty(property):  # NOQA
    def __get__(self, obj, objtype=None):
        return super(classproperty, self).__get__(objtype)


db = SQLAlchemy(app)
migrate = Migrate(app, db)


class Organization(db.Model):
    """
    Personal organizations have only one member
    Members only have one personal organization
    """
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    name = db.Column(db.Text)

    submissions = db.relationship('Submission', secondary='endpoint')
    endpoints = db.relationship('Endpoint', lazy='select', backref=db.backref('organization'))

    __repr__ = sane_repr('name')

    @validates('name')
    def validate_name(self, key, name):
        """Name must be at least 3 characters long and all lowercase"""
        if len(name) < 3:
            raise ValueError('Organization name must be more than 2 characters.')
        if name.lower() == 'destinations':
            raise ValueError('Organization name cannot be %s' % name)
        return name


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    email = db.Column(db.Text, unique=True, nullable=False)
    name = db.Column(db.Text)
    role = db.Column(db.Text, default='owner')  # owner, member

    verified = db.Column(db.Boolean, default=False)
    validation_hash = db.Column(db.Text)
    validation_hash_added = db.Column(db.DateTime)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'))

    organization = db.relationship('Organization', lazy='select', backref=db.backref('users'))

    __repr__ = sane_repr('email')

    def refresh_validation_hash(self):
        self.validation_hash = uuid.uuid4().hex
        self.validation_hash_added = datetime.datetime.now()

    def has_destination(self, cls):
        assert issubclass(cls, PersonalDestinationMixin)

        pd = cls.query.filter_by(user_id=self.id).first()
        return self.is_authenticated and pd and pd.credentials and pd.credentials.refresh_token

    def send_confirmation_email(self):
        msg = EmailMessage()
        msg.set_content('Click here to confirm your email address: {}'.format(url_for(
            'login_with_validation', validation_hash=self.validation_hash, _external=True)))
        msg['Subject'] = 'Login to FormEndpoint'
        msg['From'] = 'support@formendpoint.com'
        msg['To'] = self.email
        s = smtplib.SMTP(os.environ['SMTP_SERVER'])
        s.login(os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'])
        s.send_message(msg)
        s.quit()


class Endpoint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    name = db.Column(db.Text)
    redirect = db.Column(db.Text)  # redirect
    _referrer = db.Column(db.String)  # restrict on referrer ORIGIN POSIX regex
    strict = db.Column(db.Boolean, default=False)  # Whether non-validated fields are allowed

    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)

    submissions = db.relationship('Submission', lazy='select',
                                  backref=db.backref('endpoint', lazy='joined'))
    endpoint_destinations = db.relationship('EndpointDestination', lazy='select',
                                            backref=db.backref('endpoint', lazy='joined'))
    validators = db.relationship('Validator', lazy='select',
                                 backref=db.backref('endpoint', lazy='joined'))

    def get_fieldnames(self):
        return sorted([x[0] for x in db.engine.execute(
            'select distinct jsonb_object_keys(data) from {} where endpoint_id={}'
            .format(Submission.__tablename__, self.id)).fetchall()])


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.Text, unique=True, default=uuid.uuid4)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    # headers
    referrer = db.Column(db.Text, nullable=False)
    ip_address = db.Column(db.Text)
    user_agent = db.Column(db.Text)
    # body data
    data = db.Column(JSONB, nullable=False)

    endpoint_id = db.Column(db.Integer, db.ForeignKey('endpoint.id'), nullable=False)

    __repr__ = sane_repr('endpoint_id', 'uuid')

    def process(self):
        for ed in self.endpoint.endpoint_destinations:
            ed.process(self)


# List = multi enum
ENDPOINT_VALIDATOR_DATATYPES = ('string', 'boolean', 'integer', 'email', 'url', 'date', 'time',
                                'datetime', 'enum', 'list', 'range', 'file')


class Validator(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    referrer = db.Column(db.String, nullable=False)
    key = db.Column(db.Text, nullable=False)
    datatype = db.Column(db.Enum(name='datatypes', *ENDPOINT_VALIDATOR_DATATYPES), nullable=False)
    data = db.Column(JSONB)  # for enum, multiple, list, range constraints
    error_message = db.Column(db.Text)

    endpoint_id = db.Column(db.Integer, db.ForeignKey('endpoint.id'))


class Destination(db.Model):
    """
    Destinations exist only to hold account-level state
    Per-endpoint data
    """
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.Text, nullable=False)

    @declared_attr
    def __mapper_args__(cls):
        return {
            'polymorphic_on': cls.type,
            'polymorphic_identity': cls.__tablename__,
            'with_polymorphic': '*',
        }

    @classmethod
    def dash_to_class(cls, dashname):
        subclass = {c.__name__: c for c in cls.__subclasses__()}
        try:
            return subclass[inflection.camelize(dashname.replace('-', '_'))]
        except KeyError:
            abort(404)

    @classproperty
    def dashname(cls):
        return inflection.dasherize(inflection.underscore(cls.__name__))

    @property
    def human_name(self):
        return inflection.humanize(self.type)

    @classmethod
    def is_valid(cls, value):
        """
        Whether the value is valid for this `Destination` type.
        Separate from whether destination value is "owned" by this account.
        A way to auto-create `EndpointDestination`s from destination args.

        Args:
            value (str): where the data goes
        Returns:
            (bool)
        """
        raise NotImplemented

    def create_endpoint_destination(self, endpoint, **kwargs):
        raise NotImplemented

    def get_form(self):
        # Create destination form
        raise NotImplemented

    def process(self, post, endpoint_destination):
        raise NotImplemented


class PersonalDestinationMixin(object):
    @declared_attr
    def user_id(cls):
        return db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    @declared_attr
    def user(cls):
        return db.relationship('User', lazy='select', uselist=False,
                               backref=db.backref(cls.__tablename__))


class GoogleDestinationMixin(object):
    scope = None

    @declared_attr
    def credentials_json(cls):
        return db.Column(JSONB)

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
    def service(self):
        raise NotImplemented

    @classmethod
    def get_flow(cls, redirect_uri=None):
        flow = OAuth2WebServerFlow(
            client_id=os.environ['GOOGLE_CLIENT_ID'],
            client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
            scope=cls.scope,
            redirect_uri=redirect_uri,
        )
        flow.params['access_type'] = 'offline'
        flow.params['prompt'] = 'consent'
        return flow


class GoogleSheet(Destination, PersonalDestinationMixin, GoogleDestinationMixin):
    """
    DeveloperMetadata lets us associate form name with columns
    We track a cursor

    Appending:
    1. Look for matching DimensionRange for field names
    2. Insert new columns to the right of furtherest DimensionRange
    3. Match rows[] append with columns
    """
    scope = 'https://www.googleapis.com/auth/spreadsheets'

    id = db.Column(db.Integer, db.ForeignKey('destination.id'), primary_key=True)

    @classproperty
    def human_name(cls):
        return 'Google Sheets'

    @property
    def service(self):
        http = self.credentials.authorize(httplib2.Http())
        return discovery.build('sheets', 'v4',
                               http=http,
                               discoveryServiceUrl=GOOGLE_SHEETS_DISCOVERY_URL,
                               cache_discovery=False)

    def get_form(self, org):
        return GoogleSheetForm()

    def get_sheets(self):
        self.service.spreadsheets()

    @staticmethod
    def convert_to_column_title(num):
        title = ''
        alist = string.ascii_uppercase
        while num:
            mod = (num - 1) % 26
            num = int((num - mod) / 26)
            title += alist[mod]
        return title[::-1]

    @classmethod
    def is_valid(cls, value):
        match = GOOGLE_SHEET_URL_PATTERN.search(value)
        return match and match.group(1)

    def create_template(self, spreadsheet_id, sheet_id, columns):
        """
        spreadsheet_id(str): spreadsheet id
        title(str): spreadsheet name
        sheet_id(int): sheet id
        columns(dict): {fieldname: metadata_id, ...}
        protected(bool): warning-only protectedrange
        """
        gss = self.service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        title = gss['properties']['title']
        return {
            "spreadsheet_id": spreadsheet_id, "title": title, "sheet_id": sheet_id,
            "columns": columns, 'protected': False
        }

    def developer_metadata_location(self, sheet_id, index):
        return {
            'dimensionRange': {
                'sheetId': sheet_id,
                'dimension': 'COLUMNS',
                'startIndex': index,
                'endIndex': index + 1,
            }
        }

    def create_developer_metadata_request(self, sheet_id, fieldname, index):
        return {
            'createDeveloperMetadata': {'developerMetadata': {
                'metadataKey': 'fieldname',
                'metadataValue': fieldname,
                'location': self.developer_metadata_location(sheet_id, index),
                'visibility': 'PROJECT'
            }}}

    def create_endpoint_destination_columns(self, spreadsheet_id, sheet_id, fieldnames,
                                            furtherest_index=-1):
        insert_at_index = furtherest_index + 1

        requests = [
            {'insertDimension': {
                'range': {
                    'sheetId': sheet_id,
                    'dimension': 'COLUMNS',
                    'startIndex': insert_at_index,
                    'endIndex': insert_at_index + len(fieldnames)
                },
                'inheritFromBefore': False,
            }},
            # Insert header row
            {'updateCells': {
                'rows': [{'values': [{
                    'userEnteredValue': {'stringValue': fieldname}} for fieldname in fieldnames
                ]}],
                'start': {'sheetId': sheet_id, 'rowIndex': 0, 'columnIndex': insert_at_index},
                'fields': 'userEnteredValue.stringValue',
            }},
            # Freeze header row
            {'updateSheetProperties': {
                'properties': {
                    'sheetId': sheet_id,
                    'gridProperties': {'frozenRowCount': 1},
                },
                'fields': 'gridProperties.frozenRowCount',
            }},
        ]

        # Create field column developer metadata DimensionRanges
        for index, fieldname in enumerate(fieldnames):
            requests.append(self.create_developer_metadata_request(sheet_id, fieldname,
                                                                   insert_at_index + index))

        replies = self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={'requests': requests}).execute()['replies'][3:]

        return dict(zip(fieldnames,
                        [reply['createDeveloperMetadata']['developerMetadata']
                            for reply in replies]))

    def create_new_sheet(self, endpoint, spreadsheet_id):
        gs = self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': [{'addSheet': {
                'properties': {
                    'title': endpoint.name,
                    'tabColor': {'blue': 1}
                }
            }}]}).execute()

        return gs['replies'][0]['addSheet']['properties']['sheetId']

    def create_endpoint_destination(self, endpoint, spreadsheet_id):
        # Get all field types
        fieldnames = endpoint.get_fieldnames()

        # Create a new sheet to write to
        sheet_id = self.create_new_sheet(endpoint, spreadsheet_id)

        # Insert column names and associate developer metadata with them
        columns = self.create_endpoint_destination_columns(spreadsheet_id, sheet_id, fieldnames)

        return EndpointDestination(
            template=self.create_template(spreadsheet_id, sheet_id,
                                          {f: columns[f]['metadataId'] for f in columns}),
            destination_id=self.id, endpoint_id=endpoint.id)

    def create_row(self, developer_metadata_dict, post):
        row = defaultlist()
        d = {developer_metadata_dict[k]['location']['dimensionRange']['startIndex']: k
             for k in developer_metadata_dict}

        for i in d:
            if d[i] in post.data:
                row[i] = post.data[d[i]]
        return [{'values': [{'userEnteredValue': {'stringValue': value}} for value in row]}]

    def process(self, post, endpoint_destination):
        spreadsheet_id = endpoint_destination.template['spreadsheet_id']
        sheet_id = endpoint_destination.template['sheet_id']
        requests = []

        developer_metadata = {}

        # Get existing columns
        for k in endpoint_destination.template['columns']:
            metadata_id = endpoint_destination.template['columns'][k]
            developer_metadata[k] = self.service.spreadsheets().developerMetadata().get(
                spreadsheetId=spreadsheet_id, metadataId=metadata_id).execute()

        # Create missing columns
        indices = [developer_metadata[i]['location']['dimensionRange']['startIndex']
                   for i in developer_metadata]
        furtherest_index = max(indices, default=-1)

        fieldnames_to_create = list(post.data.keys()).copy()
        for fieldname in post.data.keys():
            metadata_id = endpoint_destination.template['columns'].get(fieldname)
            if metadata_id:
                fieldnames_to_create.remove(fieldname)

        new_developer_metadata = self.create_endpoint_destination_columns(spreadsheet_id, sheet_id,
                                                                          fieldnames_to_create,
                                                                          furtherest_index)
        developer_metadata.update(new_developer_metadata)
        template_copy = deepcopy(endpoint_destination.template)
        template_copy['columns'].update({k: new_developer_metadata[k]['metadataId']
                                         for k in new_developer_metadata})
        endpoint_destination.template = template_copy
        db.session.add(endpoint_destination)
        db.session.commit()

        # Create Rows
        rows = self.create_row(developer_metadata, post)
        requests.append({'appendCells': {
            'sheetId': sheet_id,
            'rows': rows,
            'fields': 'userEnteredValue.stringValue'
        }})

        self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': requests}
        ).execute()


class Gmail(Destination, PersonalDestinationMixin, GoogleDestinationMixin):
    scope = 'https://www.googleapis.com/auth/gmail.send email'

    id = db.Column(db.Integer, db.ForeignKey('destination.id'), primary_key=True)
    email = db.Column(db.Text)

    @classproperty
    def human_name(cls):
        return 'Gmail'

    @property
    def service(self):
        http = self.credentials.authorize(httplib2.Http())
        return discovery.build('gmail', 'v1',
                               http=http, cache_discovery=False)

    def get_form(self, org):
        # TODO
        choices = [(email, email) for email, in Gmail.query.filter(Gmail.user_id.in_(
            [u.id for u in org.users])).with_entities(Gmail.email).all()]

        return GmailForm(choices)

    def create_template(self, sender, subject, body):
        """
        subject:
        body:
        """
        return {'sender': sender, 'subject': subject, 'body': body}

    def create_endpoint_destination(self, endpoint, sender, subject, body):
        env = Environment()
        # Validate
        env.parse(subject)
        env.parse(body)

        return EndpointDestination(template=self.create_template(sender, subject, body),
                                   endpoint_id=endpoint.id,
                                   destination_id=self.id)

    def process(self, post, endpoint_destination):
        body_template = Template(endpoint_destination.template['body'])
        message = MIMEText(body_template.render(**post.data))
        message['to'] = post.data['email']
        message['subject'] = endpoint_destination.template['subject']
        self.service.users().messages().send(userId='me', body={
            'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()
        }).execute()


class EndpointDestination(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    template = db.Column(JSONB)  # jinja for emails, field to column mapping for Google Sheets

    destination_id = db.Column(db.Integer, db.ForeignKey('destination.id'), nullable=False)
    endpoint_id = db.Column(db.Integer, db.ForeignKey('endpoint.id'), nullable=False)

    __repr__ = sane_repr('destination_id', 'organization_id')

    destination = db.relationship('Destination', lazy='select', uselist=False,
                                  backref=db.backref('endpoint_destination'))

    def process(self, post):
        return self.destination.process(post, self)
