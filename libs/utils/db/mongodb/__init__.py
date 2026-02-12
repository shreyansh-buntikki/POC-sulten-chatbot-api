from pymongo import MongoClient
from pymongo.errors import PyMongoError

from libs.utils.config import FASTAPI_DATABASE_NAME, MONGO_URI, USERS_COLLECTION


def connect_db(db_name: str):
    try:
        client = MongoClient(MONGO_URI)
        return client[db_name]
    except PyMongoError as error:
        raise Exception(
            f'Failed to connect to database: "{db_name}",'
            f"ERROR: {str(error)}"
        )


fastapi_db = connect_db(FASTAPI_DATABASE_NAME)

fastapi_users_collection = fastapi_db[USERS_COLLECTION]
