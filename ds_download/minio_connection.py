import os
from dotenv import load_dotenv
from minio import Minio


def get_minio_bucket_name(is_composite: bool = False) -> str:
    return os.environ.get(
        "MINIO_BUCKET_NAME_COMPOSITES" if is_composite else "MINIO_BUCKET_NAME_PRODUCTS"
    )


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
        bucket_name (str, optional): The name of the MinIO bucket to use. Defaults to the value from the "MINIO_BUCKET_NAME_PRODUCTS" environment variable.

    Attributes:
        bucket_name (str): The name of the MinIO bucket to use.
    """
    
    def __init__(
        self, 
        host: str = None,
        port: str = None,
        access_key: str = None,
        secret_key: str = None,
        bucket_name: str = None,
    ):
        load_dotenv(".env")
        host = host or os.environ.get("MINIO_HOST")
        port = port or os.environ.get("MINIO_PORT")
        access_key = access_key or os.environ.get("MINIO_ACCESS_KEY")
        secret_key = secret_key or os.environ.get("MINIO_SECRET_KEY")
        bucket_name = bucket_name or get_minio_bucket_name()

        super().__init__(
            endpoint=f"{host}:{port}",
            access_key=access_key,
            secret_key=secret_key,
            secure=False  # Secure set to False by default
        )
        self.bucket_name = bucket_name
