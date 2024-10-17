import os
from pymongo import MongoClient
from pymongo.collection import Collection


class MongoConnection():

    def __init__(self, 
                host=os.environ.get("MONGO_HOST"), 
                port=os.environ.get("MONGO_PORT"),
                username=os.environ.get("MONGO_USERNAME"),
                password=os.environ.get("MONGO_PASSWORD"),
                database=os.environ.get("MONGO_DATABASE_NAME"),
                collection=os.environ.get("MONGO_COLLECTION_NAME"),
                composite_collection=os.environ.get("MONGO_COMPOSITE_COLLECTION_NAME")
    ):
        self.mongo_client = MongoClient(
                host = f"mongodb://{host}:{port}/",
                username = username,
                password = password
            )
        self.db = database
        self.collection = collection
        self.composite_collection = composite_collection

    def get_collection_object(self) -> Collection:
        return self.mongo_client[self.db][self.collection]
    
    def get_composite_collection_object(self) -> Collection:
        return self.mongo_client[self.db][self.composite_collection]