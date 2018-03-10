from flask_wtf import FlaskForm
from wtforms import BooleanField, HiddenField, SelectField, StringField, TextAreaField, validators
from wtforms.fields.html5 import EmailField


class LoginForm(FlaskForm):
    email = EmailField('Email', validators=[validators.DataRequired('Email required.'),
                                            validators.Email('You must enter a valid email.')])


class EndpointForm(FlaskForm):
    name = StringField('Endpoint Name', validators=[validators.DataRequired()])


class GoogleSheetForm(FlaskForm):
    file = HiddenField()
    backfill = BooleanField()


class GmailForm(FlaskForm):
    sender = SelectField('From')
    subject = StringField('Subject')
    body = TextAreaField('Template', validators=[validators.DataRequired()], render_kw={
        'placeholder': 'Template will be rendered with Jinja.',
        'cols': 40,
        'rows': 16,
    })
    backfill = BooleanField()

    def __init__(self, choices):
        super().__init__()
        self.sender.choices = choices
