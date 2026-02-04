from bson import ObjectId

from libs.utils.db.mongodb.base_repository import BaseRepository


def get_all_documents_from_collection(repository: BaseRepository):
    return list(repository.find({}))


def get_document_with_filter_from_collection(
    repository: BaseRepository, filter_doc: dict
):
    return repository.find_one(filter_doc)


def insert_document_in_collection(repository: BaseRepository, insert_doc: dict):
    return repository.insert_one(insert_doc).inserted_id


def update_document_in_collection(
    repository: BaseRepository, filter_doc: dict, update_doc: dict
):
    return repository.update_one(filter_doc, {"$set": update_doc})


def delete_document_in_collection(repository: BaseRepository, user_id: str):
    return repository.delete_one({"_id": ObjectId(user_id)})
