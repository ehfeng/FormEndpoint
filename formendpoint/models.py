from collections import UserList
import datetime
import httplib2
import inflection
import os
import re
import string
import uuid

from apiclient import discovery
from flask import abort
from flask_login import UserMixin
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from oauth2client.client import OAuth2Credentials, OAuth2WebServerFlow
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import validates
from sqlalchemy.sql import func
from sqlalchemy.sql.expression import literal

from app import app
from formendpoint.forms import GoogleSheetForm

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


class OrganizationMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    owner = db.Column(db.Boolean, default=False)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'))

    __repr__ = sane_repr('organization_id', 'user_id')


class Organization(db.Model):
    """
    Personal organizations have only one member
    Members only have one personal organization
    """
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    name = db.Column(db.String, unique=True)
    personal = db.Column(db.Boolean, default=True)

    user = db.relationship('User', lazy='select', uselist=False,
                           backref=db.backref('personal_organization'))
    endpoints = db.relationship('Endpoint', lazy='select', backref=db.backref('organization'))
    members = db.relationship('OrganizationMember', lazy='select',
                              backref=db.backref('organization'))

    __repr__ = sane_repr('name')

    @validates('name')
    def validate_name(self, key, name):
        """Name must be at least 3 characters long"""
        assert len(name) > 2
        assert name != 'destinations'
        return name


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    email = db.Column(db.Text, unique=True, nullable=False)
    verified = db.Column(db.Boolean, default=False)
    validation_hash = db.Column(db.Text)
    validation_hash_added = db.Column(db.DateTime)

    personal_organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), unique=True)

    memberships = db.relationship('OrganizationMember', lazy='select', backref=db.backref('user'))

    __repr__ = sane_repr('email')

    def refresh_validation_hash(self):
        self.validation_hash = uuid.uuid4().hex
        self.validation_hash_added = datetime.datetime.now()

    def is_owner(self, organization):
        return db.session.query(OrganizationMember.query.filter_by(
            user_id=self.id,
            owner=True,
            organization_id=organization.id
        ).exists()).scalar()

    def is_member(self, organization):
        exists_query = OrganizationMember.query.filter_by(
            user_id=self.id,
            organization_id=organization.id
        ).exists()
        return db.session.query(exists_query).scalar()

    def has_destination(self, cls):
        assert issubclass(cls, PersonalDestinationMixin)

        pd = cls.query.filter_by(user_id=self.id).first()
        return self.is_authenticated and pd and pd.credentials and pd.credentials.refresh_token


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.Text, unique=True, default=uuid.uuid4)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    # headers
    referrer = db.Column(db.Text, nullable=False)
    ip_address = db.Column(db.Text)
    user_agent = db.Column(db.Text)
    # body data
    data = db.Column(JSONB, nullable=False)

    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    endpoint_id = db.Column(db.Integer, db.ForeignKey('endpoint.id'), nullable=True)

    __repr__ = sane_repr('uuid', 'organization_id', 'endpoint_id')

    def process(self):
        for endpoint_destination in self.endpoint.endpoint_destinations:
            endpoint_destination.process(self)


class Endpoint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    uuid = db.Column(db.Text, unique=True, default=uuid.uuid4)
    name = db.Column(db.Text)
    secret = db.Column(db.Boolean, default=True)
    redirect = db.Column(db.Text)
    _referrer = db.Column(db.String)  # ORIGIN POSIX regex
    strict = db.Column(db.Boolean, default=False)  # Whether non-validated fields are allowed

    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'))

    posts = db.relationship('Post', lazy='select', backref=db.backref('endpoint', lazy='joined'))
    endpoint_destinations = db.relationship('EndpointDestination', lazy='select',
                                            backref=db.backref('endpoint', lazy='joined'))

    __repr__ = sane_repr('name', 'uuid', 'organization_id')

    @property
    def referrer(self):
        return re.sub('_', '?', re.sub('%', '*', self._referrer))

    @referrer.setter
    def referrer(self, value):
        self._referrer = re.sub('\?', '_', re.sub('\*', '%', self.value))

    @classmethod
    def create_for_destination(cls):
        """
        Automatically create a Endpoint for Google Sheet
        """
        return None

    @classmethod
    def get_for_referrer(cls, referrer, organization):
        return cls.query.filter((cls.organization == organization) &
                                (literal(referrer).ilike(cls._referrer))).all()

    @classmethod
    def get_by_referrer(cls, referrer, user):
        # TODO
        raise NotImplemented


# List = multi enum
ENDPOINT_VALIDATOR_DATATYPES = ('string', 'boolean', 'integer', 'email', 'url', 'date', 'time',
                                'datetime', 'enum', 'list', 'range', 'file')


class EndpointValidator(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.Text, nullable=False)
    datatype = db.Column(db.Enum(name='datatypes', *ENDPOINT_VALIDATOR_DATATYPES), nullable=False)
    data = db.Column(JSONB)  # for enum, multiple, list, range constraints

    endpoint_id = db.Column(db.Integer, db.ForeignKey('endpoint.id'), nullable=False)


class Destination(db.Model):
    """
    Destinations exist only to hold account-level state
    Per-endpoint data
    """
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.Text, nullable=False)

    endpoint_destinations = db.relationship('EndpointDestination', lazy='select',
                                            backref=db.backref('destination'))

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

    def form(self):
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

    @property
    def form(self):
        return GoogleSheetForm()

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

    def get_fieldnames(self, endpoint):
        return set([field for post in endpoint.posts for field in post.data.keys()])

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
                                            insert_at_index=0):
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
            requests.append(self.create_developer_metadata_request(sheet_id, fieldname, index))

        replies = self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={'requests': requests}).execute()['replies'][3:]

        return dict(zip(fieldnames,
                        [reply['createDeveloperMetadata']['developerMetadata']['metadataId']
                            for reply in replies]))

    def create_new_sheet(self, endpoint, spreadsheet_id):
        gs = self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': [{'addSheet': {
                'properties': {
                    'title': 'FormEndpoint.com/%s/%s' % (
                        endpoint.organization.name, endpoint.name
                    ),
                    'tabColor': {'blue': 1, 'red': 1}
                }
            }}]}).execute()

        return gs['replies'][0]['addSheet']['properties']['sheetId']

    def create_endpoint_destination(self, endpoint, spreadsheet_id):
        # Get all field types
        fieldnames = self.get_fieldnames(endpoint)

        # Create a new sheet to write to
        sheet_id = self.create_new_sheet(endpoint, spreadsheet_id)

        # Insert column names and associate developer metadata with them
        columns = self.create_endpoint_destination_columns(spreadsheet_id, sheet_id, fieldnames)

        return EndpointDestination(
            template=self.create_template(spreadsheet_id, sheet_id, columns),
            destination_id=self.id, endpoint_id=endpoint.id)

    def create_rows(self, developer_metadata_dict, post):
        row = defaultlist()
        d = {developer_metadata_dict[k]['location']['dimensionRange']['startIndex']: k
             for k in developer_metadata_dict}
        for i in d:
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
        furtherest_index = max([developer_metadata[i]['location']['dimensionRange']['startIndex']
                                for i in developer_metadata])

        fieldnames_to_create = list(post.data.keys()).copy()
        for fieldname in post.data.keys():
            metadata_id = endpoint_destination.template['columns'].get(fieldname)
            if metadata_id:
                fieldnames_to_create.remove(fieldname)

        new_developer_metadata = self.create_endpoint_destination_columns(spreadsheet_id, sheet_id,
                                                                          fieldnames_to_create,
                                                                          furtherest_index + 1)
        developer_metadata.update(new_developer_metadata)

        # Create Rows
        rows = self.create_rows(developer_metadata, post)

        requests.append({'appendCells': {
            'sheetId': sheet_id,
            'rows': rows,
            'fields': 'userEnteredValue.stringValue'
        }})

        self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': requests}
        ).execute()


class EndpointDestination(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    template = db.Column(JSONB)  # jinja for emails, field to column mapping for Google Sheets

    destination_id = db.Column(db.Integer, db.ForeignKey('destination.id'), nullable=False)
    endpoint_id = db.Column(db.Integer, db.ForeignKey('endpoint.id'), nullable=False)

    __repr__ = sane_repr('destination_id', 'endpoint_id')

    def process(self, post):
        return self.destination.process(post, self)
