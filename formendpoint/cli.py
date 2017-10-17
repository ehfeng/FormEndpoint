from flask import url_for
import click

from app import app
from formendpoint.models import db, Organization, OrganizationMember, User


@app.cli.command()
@click.option('--email', prompt="Email")
@click.option('--username', prompt="username")
def createuser(email, username):
    user = User(email=email)
    org = Organization(slug=username, personal=True)
    om = OrganizationMember(organization=org, user=user)
    db.session.add(user)
    db.session.add(org)
    db.session.add(om)
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
