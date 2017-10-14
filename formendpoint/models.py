import datetime
from enum import Enum
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.sql import func
from sqlalchemy.sql.expression import BinaryExpression, literal
from sqlalchemy.sql.operators import custom_op

from app import app

GOOGLE_SHEETS_DISCOVERY_URL = 'https://sheets.googleapis.com/$discovery/rest?version=v4'

db = SQLAlchemy(app)
migrate = Migrate(app, db)


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.Text, unique=True, nullable=False)
    username = db.Column(db.String, unique=True)
    verified = db.Column(db.Boolean, default=False)
    credentials_json = db.Column(JSONB)
    validation_hash = db.Column(db.Text)
    validation_hash_added = db.Column(db.DateTime)

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
    def sheets(self):
        http = self.credentials.authorize(httplib2.Http())
        return discovery.build(
                'sheets', 'v4',
                http=http,
                discoveryServiceUrl=GOOGLE_SHEETS_DISCOVERY_URL,
                cache_discovery=False,
            )

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
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=True)
    # ad-hoc destinations
    destinations = db.Column(ARRAY(db.Text))


class Form(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    name = db.Column(db.Text)
    redirect = db.Column(db.Text)
    _referrer = db.Column(db.String)  # ORIGIN POSIX regex

    @property
    def referrer(self):
        return re.sub('_', '?', re.sub('%', '*', self._referrer))

    @referrer.setter
    def referrer(self, value):
        self._referrer = re.sub('\?', '_', re.sub('\*', '%', self.value))

    @classmethod
    def create_for_destination(cls):
        """
        Automatically create a Form for Google Sheet
        """
        return None

    @classmethod
    def reverse_ilike(cls, destination):
        BinaryExpression(literal(destination), cls._referrer, custom_op('ilike'))

    @classmethod
    def get_or_create_from_destination(cls, destination, user):
        # forms = cls.query.filter(cls.user_id == user.id & cls.reverse_ilike(destination)).all()
        # filter(lambda x: x.is_valid(destination), DestinationMixin.__subclasses__())
        # TODO
        raise NotImplemented

    @classmethod
    def get_by_referrer(cls, referrer, user):
        # TODO
        return None
        raise NotImplemented


class FormDatatypes(Enum):
    string = 0
    boolean = 1
    integer = 2
    email = 3
    url = 4
    date = 5
    time = 6
    datetime = 7
    enum = 8
    list = 9
    range = 10
    file = 11


class FormValidator(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.Text, nullable=False)
    datatype = db.Column(db.Enum(FormDatatypes), nullable=False)
    data = db.Column(JSONB)  # for enum, multiple, range constraints
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


class DestinationMixin(object):
    """
    Destinations exist only to hold account-level state
    Per-form data
    """
    @declared_attr
    def __tablename__(cls):
        return camel_to_snake_case(cls.__name__)

    id = db.Column(db.Integer, primary_key=True)

    @declared_attr
    def user_id(cls):
        return db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    @classmethod
    def is_valid(cls, value):
        """
        Whether the value is valid for this `Destination` type.
        Separate from whether destination value is "owned" by this account.
        A way to auto-create `FormDestination`s from destination args.

        Args:
            value (str): where the data goes
        Returns:
            (bool)
        """
        raise NotImplemented


class Webhook(db.Model, DestinationMixin):
    id = db.Column(db.Integer, primary_key=True)
    verified_urls = db.Column(ARRAY(db.Text))
    template = db.Column(db.Text)

    @classmethod
    def is_valid(cls, value):
        """Is this a secure url"""
        url = urlparse(value)
        return url.netloc and url.scheme == 'https'


class Email(db.Model, DestinationMixin):
    template = db.Column(db.Text)

    @classmethod
    def is_valid(cls, value):
        return False


class GoogleSheet(db.Model, DestinationMixin):
    URL_PATTERN = re.compile("^https\://docs\.google\.com/spreadsheets/d/(\S+)/.*")

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
        match = cls.URL_PATTERN.search(value)
        return match and match.group(1)


class FormDestination(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    type = db.Column(db.Enum(*[c.__name__ for c in DestinationMixin.__subclasses__()]))
    object_id = db.Column(db.Integer)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
