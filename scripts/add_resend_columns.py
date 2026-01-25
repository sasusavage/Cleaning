"""Add email_provider and resend_api_key_encrypted columns to email_settings table."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

db_engine = os.getenv('DB_ENGINE', 'mysql').lower()
print(f'DB Engine: {db_engine}')

if db_engine == 'postgres':
    import psycopg2
    conn = psycopg2.connect(os.getenv('POSTGRES_URL'))
    cursor = conn.cursor()
    
    # Check existing columns
    cursor.execute("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = 'email_settings'
    """)
    columns = [row[0] for row in cursor.fetchall()]
    print('Existing columns:', columns)
    
    # Add email_provider column if missing
    if 'email_provider' not in columns:
        cursor.execute("ALTER TABLE email_settings ADD COLUMN email_provider VARCHAR(20) DEFAULT 'smtp'")
        print('Added email_provider column')
    else:
        print('email_provider column already exists')
    
    # Add resend_api_key_encrypted column if missing
    if 'resend_api_key_encrypted' not in columns:
        cursor.execute("ALTER TABLE email_settings ADD COLUMN resend_api_key_encrypted TEXT")
        print('Added resend_api_key_encrypted column')
    else:
        print('resend_api_key_encrypted column already exists')
    
    conn.commit()
    cursor.close()
    conn.close()
    print('Migration complete!')

else:
    import mysql.connector
    conn = mysql.connector.connect(
        host=os.getenv('MYSQL_HOST'),
        user=os.getenv('MYSQL_USER'),
        password=os.getenv('MYSQL_PASSWORD'),
        database=os.getenv('MYSQL_DB')
    )
    cursor = conn.cursor()
    
    # Check existing columns
    cursor.execute('DESCRIBE email_settings')
    columns = [row[0] for row in cursor.fetchall()]
    print('Existing columns:', columns)
    
    if 'email_provider' not in columns:
        cursor.execute("ALTER TABLE email_settings ADD COLUMN email_provider VARCHAR(20) DEFAULT 'smtp'")
        print('Added email_provider column')
    else:
        print('email_provider column already exists')
    
    if 'resend_api_key_encrypted' not in columns:
        cursor.execute("ALTER TABLE email_settings ADD COLUMN resend_api_key_encrypted TEXT")
        print('Added resend_api_key_encrypted column')
    else:
        print('resend_api_key_encrypted column already exists')
    
    conn.commit()
    cursor.close()
    conn.close()
    print('Migration complete!')
