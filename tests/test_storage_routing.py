import os
import unittest
from unittest.mock import patch

from ds_download.minio_connection import MinioConnection, get_minio_bucket_name
from ds_download.mongo_connection import MongoConnection


class StorageRoutingTests(unittest.TestCase):
    def test_minio_uses_product_and_composite_bucket_envs(self):
        with patch.dict(
            os.environ,
            {
                "MINIO_HOST": "localhost",
                "MINIO_PORT": "9000",
                "MINIO_ACCESS_KEY": "access",
                "MINIO_SECRET_KEY": "secret",
                "MINIO_BUCKET_NAME_PRODUCTS": "products",
                "MINIO_BUCKET_NAME_COMPOSITES": "composites",
            },
        ):
            self.assertEqual("products", MinioConnection().bucket_name)
            self.assertEqual("products", get_minio_bucket_name())
            self.assertEqual("composites", get_minio_bucket_name(is_composite=True))

    def test_mongo_uses_product_and_composite_collection_envs(self):
        with patch.dict(
            os.environ,
            {
                "MONGO_HOST": "localhost",
                "MONGO_PORT": "27017",
                "MONGO_DATABASE_NAME": "catalog",
                "MONGO_PRODUCT_COLLECTION_NAME": "products",
                "MONGO_COMPOSITE_COLLECTION_NAME": "composites",
            },
        ):
            connection = MongoConnection()

        try:
            self.assertEqual("products", connection.collection)
            self.assertEqual("composites", connection.composite_collection)
        finally:
            connection.mongo_client.close()


if __name__ == "__main__":
    unittest.main()
