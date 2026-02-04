from libs.utils.db.mongodb import fastapi_users_collection
from libs.utils.db.mongodb.base_repository import BaseRepository

fastapi_users_repository = BaseRepository(
    collection=fastapi_users_collection, timestamps=True
)
