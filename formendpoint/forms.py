from flask_wtf import FlaskForm
from wtforms import BooleanField, HiddenField, SelectField, StringField, TextAreaField, validators


class EndpointForm(FlaskForm):
    name = StringField('Endpoint Name', validators=[validators.DataRequired()])


class GoogleSheetForm(FlaskForm):
    file = HiddenField()
    backfill = BooleanField()


class GmailForm(FlaskForm):
    sender = SelectField('From')
    subject = StringField('subject')
    body = TextAreaField('Email template', validators=[validators.DataRequired()])

    def __init__(self, choices):
        super().__init__()
        self.sender.choices = choices
