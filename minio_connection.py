from minio import Minio
import os

class MinioConnection(Minio):
    "A class including handled MinIO methods"
    def __init__(self, 
                host=os.environ.get("MINIO_HOST"),
                port=os.environ.get("MINIO_PORT"),
                access_key=os.environ.get("MINIO_ACCESS_KEY"),
                secret_key=os.environ.get("MINIO_SECRET_KEY"),
                bucket_name=os.environ.get("MINIO_BUCKET_NAME")
    ):
        super().__init__(
                    endpoint = f"{host}:{port}",
                    access_key = access_key,
                    secret_key = secret_key,
                    secure = False
        )

        self.bucket_name = bucket_name