from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, validators

from formendpoint.models import DestinationMixin


class EndpointForm(FlaskForm):
    name = StringField('name', validators=[validators.DataRequired()])
    secret = BooleanField('secret', default=True, validators=[validators.DataRequired()])


class EndpointDestinationForm(FlaskForm):
    destination = SelectField(
        choices=[(c.__name__, c.__name__) for c in DestinationMixin.__subclasses__()])
