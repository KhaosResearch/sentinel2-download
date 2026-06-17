import os
from pymongo import MongoClient
from pymongo.collection import Collection
from dotenv import load_dotenv
load_dotenv()

class MongoConnection:
    """
    A class to handle MongoDB connections and provide access to collections.

    This class connects to a MongoDB database using the connection details retrieved 
    from environment variables. It provides methods to access both a main collection 
    and a composite collection.

    Args:
        host (str, optional): The MongoDB host. Defaults to the value from the "MONGO_HOST" environment variable.
        port (str, optional): The MongoDB port. Defaults to the value from the "MONGO_PORT" environment variable.
        username (str, optional): The MongoDB username. Defaults to the value from the "MONGO_USERNAME" environment variable.
        password (str, optional): The MongoDB password. Defaults to the value from the "MONGO_PASSWORD" environment variable.
        database (str, optional): The MongoDB database name. Defaults to the value from the "MONGO_DATABASE_NAME" environment variable.
        collection (str, optional): The name of the MongoDB collection to use. Defaults to the value from the "MONGO_COLLECTION_NAME" environment variable.
        composite_collection (str, optional): The name of the MongoDB composite collection to use. Defaults to the value from the "MONGO_COMPOSITE_COLLECTION_NAME" environment variable.

    Attributes:
        mongo_client (MongoClient): The MongoDB client instance.
        db (str): The name of the database.
        collection (str): The name of the main collection.
        composite_collection (str): The name of the composite collection.
    """

    def __init__(
        self, 
        host: str = None, 
        port: str = None,
        username: str = None,
        password: str = None,
        database: str = None,
        collection: str = None,
        composite_collection: str = None
    ):
        host = host or os.environ.get("MONGO_HOST")
        port = port or os.environ.get("MONGO_PORT")
        username = username or os.environ.get("MONGO_USERNAME")
        password = password or os.environ.get("MONGO_PASSWORD")
        database = database or os.environ.get("MONGO_DATABASE_NAME")
        collection = collection or os.environ.get("MONGO_COLLECTION_NAME")
        composite_collection = composite_collection or os.environ.get("MONGO_COMPOSITE_COLLECTION_NAME")

        self.mongo_client = MongoClient(
            host=f"mongodb://{host}:{port}/",
            username=username,
            password=password
        )
        self.db = database
        self.collection = collection
        self.composite_collection = composite_collection

    def get_collection_object(self) -> Collection:
        """
        Get the main collection object.

        Returns:
            Collection: The MongoDB collection object for the main collection.
        """
        return self.mongo_client[self.db][self.collection]
    
    def get_composite_collection_object(self) -> Collection:
        """
        Get the composite collection object.

        Returns:
            Collection: The MongoDB collection object for the composite collection.
        """
        return self.mongo_client[self.db][self.composite_collection]
