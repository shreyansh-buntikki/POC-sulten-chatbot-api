# from os import path
#
# from dotenv import dotenv_values
#
# env_path = ".env"
#
# if not path.exists(env_path):
#     raise Exception(".env request_file not found")
#
# config = dotenv_values(env_path)
# HOST = config.get("HOST")
#
# FASTAPI_PORT = int(config.get("FASTAPI_PORT"))
#
# GUNICORN_WORKERS = config.get("GUNICORN_WORKERS")
#
# MONGO_URI = config.get("MONGO_URI")
#
# FASTAPI_DATABASE_NAME = config.get("FASTAPI_DATABASE_NAME")
#
# TEAMS_WEBHOOK_URL = config.get("TEAMS_WEBHOOK_URL")
# ENVIRONMENT = config.get("ENVIRONMENT", "development")
import os
from os import path
from dotenv import load_dotenv, dotenv_values

env_path = ".env"

# Load .env if present (local dev)
file_config = {}
if path.exists(env_path):
    load_dotenv(env_path)
    file_config = dotenv_values(env_path)

# Helper: env var > .env file fallback
def get(key, default=None):
    return os.getenv(key) or file_config.get(key, default)

HOST = get("HOST")
FASTAPI_PORT = int(get("FASTAPI_PORT", 8000))
GUNICORN_WORKERS = get("GUNICORN_WORKERS")
MONGO_URI = get("MONGO_URI")
FASTAPI_DATABASE_NAME = get("FASTAPI_DATABASE_NAME")
TEAMS_WEBHOOK_URL = get("TEAMS_WEBHOOK_URL")
ENVIRONMENT = get("ENVIRONMENT", "development")
