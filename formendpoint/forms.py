from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, TextAreaField, validators


class EndpointForm(FlaskForm):
    name = StringField('name', validators=[validators.DataRequired()])
    secret = BooleanField('secret', default=False)


class GoogleSheetForm(FlaskForm):
    spreadsheet = SelectField('Destination spreadsheet', choices=[('', 'Create new spreadsheet')])


class GmailForm(FlaskForm):
    sender = StringField('From', validators=[validators.Email('must be an email address')])
    recipient = StringField('To', validators=[validators.Email('must be an email address')])
    body = TextAreaField('Email template')
