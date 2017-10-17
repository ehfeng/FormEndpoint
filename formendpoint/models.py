import datetime
import httplib2
import re
import string
import uuid
from urllib.parse import urlparse

from apiclient import discovery
from flask_login import UserMixin
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_sqlalchemy.model import camel_to_snake_case
from oauth2client.client import OAuth2Credentials
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.sql import func
from sqlalchemy.sql.expression import literal

from app import app

GOOGLE_SHEETS_DISCOVERY_URL = 'https://sheets.googleapis.com/$discovery/rest?version=v4'
GOOGLE_SHEET_URL_PATTERN = re.compile("^https\://docs\.google\.com/spreadsheets/d/(\S+)/.*")

db = SQLAlchemy(app)
migrate = Migrate(app, db)


class OrganizationMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'))


class Organization(db.Model):
    """
    Personal organizations have only one member
    Members only have one personal organization
    """
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    slug = db.Column(db.String, unique=True)

    endpoints = db.relationship('Endpoint', lazy='select', backref=db.backref('organization'))
    members = db.relationship('OrganizationMember', lazy='select',
                              backref=db.backref('organization'))


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    email = db.Column(db.Text, unique=True, nullable=False)
    verified = db.Column(db.Boolean, default=False)
    validation_hash = db.Column(db.Text)
    validation_hash_added = db.Column(db.DateTime)
    profile_organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), unique=True)

    memberships = db.relationship('OrganizationMember', lazy='select', backref=db.backref('user'))

    def refresh_validation_hash(self):
        self.validation_hash = uuid.uuid4().hex
        self.validation_hash_added = datetime.datetime.now()

    def __repr__(self):
        return '<%s>' % self.email


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    # headers
    referrer = db.Column(db.Text, nullable=False)
    ip_address = db.Column(db.Text)
    user_agent = db.Column(db.Text)
    # body data
    data = db.Column(JSONB, nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    endpoint_id = db.Column(db.Integer, db.ForeignKey('endpoint.id'), nullable=True)


class Endpoint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    slug = db.Column(db.Text)
    redirect = db.Column(db.Text)
    _referrer = db.Column(db.String)  # ORIGIN POSIX regex
    strict = db.Column(db.Boolean)  # Whether non-validated fields are allowed
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'))

    posts = db.relationship('Post', lazy='select', backref=db.backref('endpoint', lazy='joined'))
    destinations = db.relationship('EndpointDestination', lazy='select',
                                   backref=db.backref('endpoint', lazy='joined'))

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


ENDPOINT_VALIDATOR_DATATYPES = ('string', 'boolean', 'integer', 'email', 'url', 'date', 'time',
                                'datetime', 'enum', 'list', 'range', 'file')


class EndpointValidator(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.Text, nullable=False)
    datatype = db.Column(db.Enum(name='datatypes', *ENDPOINT_VALIDATOR_DATATYPES), nullable=False)
    data = db.Column(JSONB)  # for enum, multiple, range constraints
    endpoint_id = db.Column(db.Integer, db.ForeignKey('endpoint.id'), nullable=False)


class DestinationMixin(object):
    """
    Destinations exist only to hold account-level state
    Per-endpoint data
    """
    @declared_attr
    def __tablename__(cls):
        return camel_to_snake_case(cls.__name__)

    id = db.Column(db.Integer, primary_key=True)

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

    def process(self, post):
        raise NotImplemented


class PersonalDestinationMixin(DestinationMixin):
    credentials_json = db.Column(JSONB)

    @declared_attr
    def user_id(cls):
        return db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

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


class Gmail(db.Model, PersonalDestinationMixin):
    email = db.Column(db.Text)


class GoogleSheet(db.Model, PersonalDestinationMixin):
    @property
    def sheets(self):
        http = self.credentials.authorize(httplib2.Http())
        return discovery.build(
                'sheets', 'v4',
                http=http,
                discoveryServiceUrl=GOOGLE_SHEETS_DISCOVERY_URL,
                cache_discovery=False,
            )

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

    @staticmethod
    def find_furthest_empty_row(data, ranges):
        return

    @staticmethod
    def convert_to_column_title(num):
        title = ''
        alist = string.ascii_uppercase
        while num:
            mod = (num-1) % 26
            num = int((num - mod) / 26)
            title += alist[mod]
        return title[::-1]

    @classmethod
    def is_valid(cls, value):
        match = GOOGLE_SHEET_URL_PATTERN.search(value)
        return match and match.group(1)


class Webhook(db.Model, DestinationMixin):
    url = db.Column(db.Text)
    verified = db.Column(db.Boolean)

    @classmethod
    def is_valid(cls, value):
        """Is this a secure url"""
        url = urlparse(value)
        return url.netloc and url.scheme == 'https'


class Email(db.Model, DestinationMixin):
    sender = db.Column(db.Text)  # verified sender
    verified = db.Column(db.Boolean)

    @classmethod
    def is_valid(cls, value):
        return False


class EndpointDestination(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    template = db.Column(db.Text)
    type = db.Column(db.Enum(name='types',
                             *[c.__name__ for c in DestinationMixin.__subclasses__()]))
    destination_id = db.Column(db.Integer)
    endpoint_id = db.Column(db.Integer, db.ForeignKey('endpoint.id'), nullable=False)
