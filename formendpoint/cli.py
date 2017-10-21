from flask import url_for
import click

from app import app
from formendpoint.models import db, Organization, OrganizationMember, User


@app.cli.command()
@click.option('--email', prompt="Email")
@click.option('--orgname', prompt="orgname")
def createuser(email, orgname):
    org = Organization(name=orgname, personal=True)
    db.session.add(org)
    db.session.commit()
    user = User(email=email, personal_organization_id=org.id)
    om = OrganizationMember(organization=org, user=user, owner=True)
    db.session.add(user)
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
