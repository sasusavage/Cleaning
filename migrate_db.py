import os
import psycopg2
import mysql.connector
from urllib.parse import urlparse

def migrate():
    # Get configuration from environment variables (similar to config.py)
    db_engine = os.environ.get('DB_ENGINE', 'postgres').strip().lower()
    postgres_url = os.environ.get('POSTGRES_URL', '').strip()
    
    # Connection details for MySQL as fallback or if explicitly set
    mysql_host = os.environ.get('MYSQL_HOST', 'localhost')
    mysql_user = os.environ.get('MYSQL_USER', 'root')
    mysql_password = os.environ.get('MYSQL_PASSWORD', '')
    mysql_db = os.environ.get('MYSQL_DB', 'database')
    mysql_port = int(os.environ.get('MYSQL_PORT', 3306))

    sql_commands = [
        """
        ALTER TABLE service_pricing_tiers
          ADD COLUMN IF NOT EXISTS min_hours DECIMAL(5,2) DEFAULT NULL,
          ADD COLUMN IF NOT EXISTS staff_label VARCHAR(100) DEFAULT NULL,
          ADD COLUMN IF NOT EXISTS hours_label VARCHAR(100) DEFAULT NULL;
        """
    ]

    conn = None
    try:
        if 'postgres' in db_engine and postgres_url:
            print(f"Connecting to PostgreSQL at {urlparse(postgres_url).hostname}...")
            conn = psycopg2.connect(postgres_url)
        else:
            print(f"Connecting to MySQL at {mysql_host}...")
            conn = mysql.connector.connect(
                host=mysql_host,
                user=mysql_user,
                password=mysql_password,
                database=mysql_db,
                port=mysql_port
            )
        
        cursor = conn.cursor()
        
        for sql in sql_commands:
            print(f"Executing: {sql.strip().splitlines()[0]}...")
            cursor.execute(sql)
        
        conn.commit()
        print("Migration completed successfully.")
        
    except Exception as e:
        print(f"Migration failed: {e}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    migrate()
