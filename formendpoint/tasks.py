from app import celery


@celery.task()
def process_post_request(data, request_args):
    """
    `destination`
    Args:
        form_id(int): Form.id
        request_args(dict):
    """
    # TODO
    raise NotImplemented


# @celery.task()
# def insert_form(user_id, spreadsheet_id, form_data):
#     user = User.query.get(user_id)
#     spreadsheet = user.sheets.spreadsheets().get(
#         spreadsheetId=spreadsheet_id,
#         includeGridData=True).execute()

#     columnar_named_ranges = {
#         r['name']: r['namedRangeId']
#         for r in spreadsheet.get('namedRanges', [])
#         if r['range'].get('startRowIndex') is None
#     }

#     if columnar_named_ranges:
#         # TODO
#         pass

#     else:
#         first_row = next(iter(spreadsheet['sheets'][0]['data'][0].get(
#             'rowData', [])), None)
#         if first_row:
#             sheet_column_headers = [
#                 c.get('effectiveValue', {}).get('stringValue', None)
#                 for c in first_row['values']
#             ]
#             append_row = []
#             for header in sheet_column_headers:
#                 append_row.append(form_data.get(header, None))

#             body = {
#                 'majorDimension': 'ROWS',
#                 'values': [append_row]
#             }

#             user.sheets.spreadsheets().values().append(
#                 spreadsheetId=spreadsheet_id,
#                 range='A:%s' % GoogleSheet.convert_to_column_title(
#                     len(sheet_column_headers)),
#                 valueInputOption='RAW',
#                 body=body).execute()
