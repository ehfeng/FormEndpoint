import datetime
from enum import Enum
import httplib2
import string
import uuid

from apiclient import discovery
from flask_login import UserMixin
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from oauth2client.client import OAuth2Credentials
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.sql import func

from app import app

GOOGLE_SHEETS_DISCOVERY_URL = 'https://sheets.googleapis.com/$discovery/\
    rest?version=v4'

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


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    submitted = db.Column(
        db.DateTime, server_default=func.now(), nullable=False)
    origin = db.Column(db.Text, nullable=False)
    data = db.Column(db.JSON, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=True)


class Form(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    patterns = db.Column(db.ARRAY(db.String))


class FormDatatypes(Enum):
    string = 0


class FormValidator(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.Text, nullable=False)
    datatype = db.Column(db.Enum(FormDatatypes), nullable=False)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


class Destinations(Enum):
    google_sheets = 0


class FormDestination(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    type = db.Column(db.Enum(Destinations))
    object_id = db.Column(db.Integer)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


class DestinationMixin(object):
    """
    Destinations exist only to hold account-level state
    Per-form data
    """
    @declared_attr
    def __tablename__(cls):
        return cls.__name__.lower()

    id = db.Column(db.Integer, primary_key=True)

    @declared_attr
    def user_id(cls):
        return db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


class Webhook(db.Model, DestinationMixin):
    id = db.Column(db.Integer, primary_key=True)
    template = db.Column(db.Text)


class Email(db.Model, DestinationMixin):
    template = db.Column(db.Text)


class GoogleSheet(db.Model, DestinationMixin):

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
