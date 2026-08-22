import os

SQLALCHEMY_DATABASE_URI = (
    f"{os.environ.get('DATABASE_DIALECT', 'postgresql+psycopg2')}://"
    f"{os.environ['DATABASE_USER']}:{os.environ['DATABASE_PASSWORD']}"
    f"@{os.environ['DATABASE_HOST']}:{os.environ.get('DATABASE_PORT', '5432')}"
    f"/{os.environ['DATABASE_DB']}"
)
SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

# Без Redis: учебный стенд, кэш в памяти процесса
CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache"}
FILTER_STATE_CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache"}
EXPLORE_FORM_DATA_CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache"}
RATELIMIT_STORAGE_URI = "memory://"
