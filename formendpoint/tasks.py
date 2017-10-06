from app import app

def make_celery(app):
    celery = Celery(app.import_name, backend=app.config['CELERY_RESULT_BACKEND'],
                    broker=app.config['CELERY_BROKER_URL'])
    celery.conf.update(app.config)
    TaskBase = celery.Task
    class ContextTask(TaskBase):
        abstract = True
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)
    celery.Task = ContextTask
    return celery

celery = make_celery(app)

@celery.task()
def insert_form(user_id, spreadsheet_id, form_data):
    user = User.query.get(user_id)
    spreadsheet = user.sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        includeGridData=True).execute()

    columnar_named_ranges = {
        r['name']: r['namedRangeId'] for r in spreadsheet.get('namedRanges', [])
        if r['range'].get('startRowIndex') == None
    }

    if columnar_named_ranges:
        furthest_row = GoogleSheet.find_furthest_empty_row(spreadsheet['sheets']['data'], columnar_named_ranges)
        # update_value_ranges = GoogleSheet.insert_form(row=furthest_row, ranges=columnar_named_ranges)

    else:
        first_row = next(iter(spreadsheet['sheets'][0]['data'][0].get('rowData', [])), None)
        if first_row:
            sheet_column_headers = [c.get('effectiveValue', {}).get('stringValue', None) for c in first_row['values']]
            append_row = []
            for header in sheet_column_headers:
                append_row.append(form_data.get(header, None))

            body = {
                'majorDimension': 'ROWS',
                'values': [append_row]
            }

            user.sheets.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range='A:%s' % GoogleSheet.convert_to_column_title(len(sheet_column_headers)),
                valueInputOption='RAW',
                body=body).execute()



