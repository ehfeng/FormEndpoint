from flask_wtf import FlaskForm
from wtforms import BooleanField, HiddenField, SelectField, StringField, TextAreaField, validators


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
