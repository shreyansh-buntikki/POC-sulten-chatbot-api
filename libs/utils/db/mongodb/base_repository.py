from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from pymongo.cursor import Cursor
from pymongo.results import (
    DeleteResult,
    InsertManyResult,
    InsertOneResult,
    UpdateResult,
)


class BaseRepository:
    def __init__(self, collection, timestamps: bool = False):
        self.collection = collection
        self.timestamps = timestamps

    def __add_timestamps(self, doc: dict):
        if self.timestamps:
            current_time = datetime.now(timezone.utc)
            doc.update({"createdAt": current_time, "updatedAt": current_time})

    def __update_timestamps(self, doc: dict, upsert: bool):
        if not self.timestamps:
            return

        current_time = datetime.now(timezone.utc)

        if "$set" in doc:
            doc["$set"].update({"updatedAt": current_time})
        else:
            doc["$set"] = {"updatedAt": current_time}

        if not upsert:
            return

        if "$setOnInsert" in doc:
            doc["$setOnInsert"].update({"createdAt": current_time})
        else:
            doc["$setOnInsert"] = {"createdAt": current_time}

    def get_name(self):
        return self.collection.name

    def insert_one(self, doc: dict) -> InsertOneResult:
        self.__add_timestamps(doc)
        return self.collection.insert_one(doc)

    def insert_many(self, docs: dict) -> InsertManyResult:
        for doc in docs:
            self.__add_timestamps(doc)
        return self.collection.insert_many(docs)

    def find_one(self, query: dict, projection: dict = None) -> dict:
        if projection is None:
            projection = {}
        return self.collection.find_one(query, projection)

    def find(
        self,
        query: dict = None,
        projection: dict = None,
        limit: int = 0,
        sort_key: str = None,
        sort_type: 1 | -1 = None,
    ) -> Cursor[dict]:
        if query is None:
            query = {}
        if projection is None:
            projection = {}

        if sort_key is not None:
            self.collection.find(query, projection).sort(sort_key, sort_type)
        return self.collection.find(query, projection).limit(limit)

    def update_one(
        self, query: dict, update: dict, upsert: bool = False
    ) -> UpdateResult:
        self.__update_timestamps(update, upsert)
        return self.collection.update_one(query, update, upsert)

    def update_many(
        self, query: dict, update: dict, upsert: bool = False
    ) -> UpdateResult:
        self.__update_timestamps(update, upsert)
        return self.collection.update_many(query, update, upsert)

    def count_documents(self, query: dict = None) -> int:
        if query is None:
            query = {}
        return self.collection.count_documents(query)

    def estimated_document_count(self) -> int:
        return self.collection.estimated_document_count()

    def delete_one(self, query: dict) -> DeleteResult:
        return self.collection.delete_one(query)

    def delete_many(self, query: dict) -> DeleteResult:
        return self.collection.delete_many(query)

    def aggregate(
        self,
        pipeline: Sequence[Mapping[str, Any]],
        allow_disk_use: bool = False,
    ) -> Cursor[dict]:
        return self.collection.aggregate(pipeline, allowDiskUse=allow_disk_use)
