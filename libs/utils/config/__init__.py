from os import path

from dotenv import dotenv_values

env_path = ".env"

if not path.exists(env_path):
    raise Exception(".env request_file not found")

config = dotenv_values(env_path)
HOST = config.get("HOST")

FASTAPI_PORT = int(config.get("FASTAPI_PORT"))

GUNICORN_WORKERS = config.get("GUNICORN_WORKERS")

MONGO_URI = config.get("MONGO_URI")

FASTAPI_DATABASE_NAME = config.get("FASTAPI_DATABASE_NAME")

TEAMS_WEBHOOK_URL = config.get("TEAMS_WEBHOOK_URL")
ENVIRONMENT = config.get("ENVIRONMENT", "development")
