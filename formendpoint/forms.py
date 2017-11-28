from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, TextAreaField, validators


class EndpointForm(FlaskForm):
    name = StringField('name', validators=[validators.DataRequired()])
    secret = BooleanField('secret', default=False)


class GoogleSheetForm(FlaskForm):
    spreadsheet = SelectField('Destination spreadsheet', choices=[('', 'Create new spreadsheet')])


class GmailForm(FlaskForm):
    sender = SelectField('From')
    subject = StringField('subject')
    body = TextAreaField('Email template', validators=[validators.DataRequired()])

    def __init__(self, choices):
        super().__init__()
        self.sender.choices = choices
