from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, validators


class EndpointForm(FlaskForm):
    name = StringField('name', validators=[validators.DataRequired()])
    secret = BooleanField('secret', validators=[validators.DataRequired()])
