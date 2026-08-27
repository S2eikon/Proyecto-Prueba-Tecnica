import psycopg2

from config import Config


def get_connection():
    return psycopg2.connect(
        host=Config.PGHOST,
        port=Config.PGPORT,
        user=Config.PGUSER,
        password=Config.PGPASSWORD,
        dbname=Config.PGDATABASE,
    )