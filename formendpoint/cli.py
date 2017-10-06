@app.cli.command()
@click.option('--email', prompt="Email")
@click.option('--username', prompt="username")
def createuser(email, username):
    db.session.add(User(email=email, username=username))
    db.session.commit()


@app.cli.command()
@click.option('--email', prompt="Email")
def login(email):
    user = User.query.filter_by(email=email).first()
    if user:
        user.refresh_validation_hash()
        db.session.add(user)
        db.session.commit()
        click.echo('Login at %s' % url_for('login', validation_hash=user.validation_hash))
    else:
        click.echo('%s doesn\'t exist. Run `flask createuser`')
