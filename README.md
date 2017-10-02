# FormEndpoint

*POST forms to Google Sheets with no backend code*

## Setup

1. Set the form `action` to your FormEndpoint URL. 
2. Add a hidden input with `name=_spreadsheet_id` and `value` of your Google Sheet URL.
3. Deploy!

## Example

```html
<form action="https://formendpoint.com/demo" method="POST">
    <input type="hidden" name="_spreadsheet_id" value="https://docs.google.com/spreadsheets/d/1QWeHPvZW4atIZxobdVXr3IYl8u4EnV99Dm_K4yGfo_8/">
    <input type="text" name="email">

    <button type="submit"></button>
</form>
```
