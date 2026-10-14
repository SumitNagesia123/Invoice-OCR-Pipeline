
import json
from src.db import get_connection, row_to_dict

def get_db_connection():
    return get_connection()

def export_data():
    conn = get_db_connection()
    c = conn.cursor()

    # Get all documents
    c.execute('SELECT * FROM invoice_documents ORDER BY processed_at DESC')
    docs = [row_to_dict(c, row) for row in c.fetchall()]

    # Enrich with line items
    for doc in docs:
        c.execute('SELECT * FROM invoice_line_items WHERE document_id = %s', (doc['document_id'],))
        doc['line_items'] = [row_to_dict(c, row) for row in c.fetchall()]

    conn.close()

    # Write to JSON
    with open('dashboard_data.json', 'w') as f:
        json.dump({'documents': docs}, f, indent=2, default=str)
    print('Exported dashboard_data.json successfully.')

if __name__ == '__main__':
    export_data()
