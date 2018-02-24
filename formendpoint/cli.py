from flask import url_for
import click

from app import app
from formendpoint.models import db, Organization, User


@app.cli.command()
@click.option('--email', prompt="Email")
@click.option('--name', prompt="Organization Name")
def createuser(email, name):
    org = Organization(name=name)
    db.session.add(org)
    db.session.commit()
    user = User(email=email, organization_id=org.id, role='owner')
    db.session.add(user)
    db.session.commit()


@app.cli.command()
@click.option('--email', prompt="Email")
def login(email):
    user = User.query.filter_by(email=email).first()
    if user:
        user.refresh_validation_hash()
        db.session.add(user)
        db.session.commit()
        click.echo('Login at %s' %
                   url_for('login', validation_hash=user.validation_hash)
                   )
    else:
        click.echo('%s doesn\'t exist. Run `flask createuser`')
