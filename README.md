# FormEndpoint

POST forms to Google Sheets with no backend code.

1. Add your form

```html
<form method="POST" action="https://formendpoint.com/demo">
    <input type="hidden" name="spreadsheet_id" value="https://docs.google.com/spreadsheets/d/1QWeHPvZW4atIZxobdVXr3IYl8u4EnV99Dm_K4yGfo_8/">

    <input type="text" name="email">
    <button type="submit"></button>
</form>
```

2. Collect submissions
