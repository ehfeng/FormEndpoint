import datetime
import httplib2
import inflection
import re
import string
import uuid
from urllib.parse import urlparse

from apiclient import discovery
from flask_login import UserMixin
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from oauth2client.client import OAuth2Credentials
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import validates
from sqlalchemy.sql import func
from sqlalchemy.sql.expression import literal

from app import app

GOOGLE_SHEETS_DISCOVERY_URL = 'https://sheets.googleapis.com/$discovery/rest?version=v4'
GOOGLE_SHEET_URL_PATTERN = re.compile("^https\://docs\.google\.com/spreadsheets/d/(\S+)/.*")


def sane_repr(*attrs):
    if 'id' not in attrs:
        attrs = ('id', ) + attrs

    def _repr(self):
        cls = type(self).__name__
        pairs = ('%s=%s' % (a, repr(getattr(self, a, None))) for a in attrs)
        return u'<%s at 0x%x: %s>' % (cls, id(self), ', '.join(pairs))

    return _repr


class classproperty(property):
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
    def validate_name(self, key, email):
        """Name must be at least 3 characters long"""
        assert len(email) > 2
        return email


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
        return db.session.query(OrganizationMember.query.filter_by(
                user_id=self.id,
                organization_id=organization.id
            ).exists()).scalar()

    def has_destination(self, cls):
        assert issubclass(cls, PersonalDestinationMixin)

        pd = cls.query.filter_by(user_id=self.id).first()
        return (self.is_authenticated and pd and pd.credentials
                and pd.credentials.refresh_token)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.Text, unique=True, default=uuid.uuid4().hex)
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


class Endpoint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    uuid = db.Column(db.Text, unique=True, default=uuid.uuid4().hex)
    name = db.Column(db.Text)
    secret = db.Column(db.Boolean, default=True)
    redirect = db.Column(db.Text)
    _referrer = db.Column(db.String)  # ORIGIN POSIX regex
    strict = db.Column(db.Boolean, default=False)  # Whether non-validated fields are allowed

    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'))

    posts = db.relationship('Post', lazy='select', backref=db.backref('endpoint', lazy='joined'))
    destinations = db.relationship('EndpointDestination', lazy='select',
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


class DestinationMixin(object):
    """
    Destinations exist only to hold account-level state
    Per-endpoint data
    """
    id = db.Column(db.Integer, primary_key=True)

    @classmethod
    def dash_to_class(cls, dashname):
        subclass = {c.__name__: c for c in DestinationMixin.__subclasses__()}
        return subclass[inflection.camelize(dashname.replace('-', '_'))]

    @classproperty
    def dashname(cls):
        return inflection.dasherize(inflection.underscore(cls.__name__))

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


class PersonalDestinationMixin(object):
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


class Gmail(db.Model, DestinationMixin, PersonalDestinationMixin):
    email = db.Column(db.Text)

    user = db.relationship('User', lazy='select', uselist=False, backref=db.backref('gmail'))

    def process(self, post):
        raise NotImplemented


class GoogleSheet(db.Model, DestinationMixin, PersonalDestinationMixin):
    user = db.relationship('User', lazy='select',
                           backref=db.backref('google_sheet', uselist=False))

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

    def process(self, post):
        raise NotImplemented


class Webhook(db.Model, DestinationMixin):
    url = db.Column(db.Text)
    verified = db.Column(db.Boolean)

    @classmethod
    def is_valid(cls, value):
        """Is this a secure url"""
        url = urlparse(value)
        return url.netloc and url.scheme == 'https'

    def process(self, post):
        raise NotImplemented


class Email(db.Model, DestinationMixin):
    sender = db.Column(db.Text)  # verified sender
    verified = db.Column(db.Boolean)

    @classmethod
    def is_valid(cls, value):
        return False

    def process(self, post):
        raise NotImplemented


class EndpointDestination(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    template = db.Column(db.Text)
    type = db.Column(db.Enum(name='types',
                             *[c.__name__ for c in DestinationMixin.__subclasses__()]))
    destination_id = db.Column(db.Integer)
    endpoint_id = db.Column(db.Integer, db.ForeignKey('endpoint.id'), nullable=False)
