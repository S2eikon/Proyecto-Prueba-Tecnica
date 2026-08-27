import os

from dotenv import load_dotenv


# Carga las variables del archivo .env
load_dotenv()


class Config:
    PGHOST = os.getenv("PGHOST", "localhost")
    PGPORT = int(os.getenv("PGPORT", "5432"))
    PGUSER = os.getenv("PGUSER", "polizas_app")
    PGPASSWORD = os.getenv("PGPASSWORD")
    PGDATABASE = os.getenv("PGDATABASE", "polizas")

    APP_PORT = int(os.getenv("APP_PORT", "8000"))
    APP_ENV = os.getenv("APP_ENV", "development")
    APP_TZ = os.getenv("APP_TZ", "America/Bogota")