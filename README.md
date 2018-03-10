# FormEndpoint

*POST forms to Google Sheets with no backend code*

## Usage

1. Set the form `action` to your FormEndpoint profile.

```html
<form action="https://formendpoint.com/1" method="POST">
   <!-- add inputs -->
</form>
```

2. Point your form at a destination like Google Sheets

3. Done!

![Example](https://github.com/ehfeng/FormEndpoint/raw/master/static/out.gif)

## Development

`source .env` (See `.sample_env` for required environment variables)

`flask run`

`watchmedo shell-command --patterns="*.py" --recursive --command='pkill -f celery; celery -A formendpoint.tasks.celery worker'`

`sass --watch static/styles/main.scss:static/build/main.css`
