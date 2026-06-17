import os
from minio import Minio
from dotenv import load_dotenv
load_dotenv()

class MinioConnection(Minio):
    """
    A class to handle MinIO connections and operations.

    This class extends the Minio client and simplifies the connection to a MinIO server
    by automatically retrieving the connection parameters (host, port, access key, secret key)
    from environment variables.

    Args:
        host (str, optional): The host address of the MinIO server. Defaults to the value from the "MINIO_HOST" environment variable.
        port (str, optional): The port on which the MinIO server is running. Defaults to the value from the "MINIO_PORT" environment variable.
        access_key (str, optional): The access key for the MinIO server. Defaults to the value from the "MINIO_ACCESS_KEY" environment variable.
        secret_key (str, optional): The secret key for the MinIO server. Defaults to the value from the "MINIO_SECRET_KEY" environment variable.
        bucket_name (str, optional): The name of the MinIO bucket to use. Defaults to the value from the "MINIO_BUCKET_NAME" environment variable.

    Attributes:
        bucket_name (str): The name of the MinIO bucket to use.
    """
    
    def __init__(
        self, 
        host: str = None,
        port: str = None,
        access_key: str = None,
        secret_key: str = None,
        bucket_name: str = None
    ):
        host = host or os.environ.get("MINIO_HOST")
        port = port or os.environ.get("MINIO_PORT")
        access_key = access_key or os.environ.get("MINIO_ACCESS_KEY")
        secret_key = secret_key or os.environ.get("MINIO_SECRET_KEY")
        bucket_name = bucket_name or os.environ.get("MINIO_BUCKET_NAME")

        super().__init__(
            endpoint=f"{host}:{port}",
            access_key=access_key,
            secret_key=secret_key,
            secure=False  # Secure set to False by default
        )
        self.bucket_name = bucket_name
