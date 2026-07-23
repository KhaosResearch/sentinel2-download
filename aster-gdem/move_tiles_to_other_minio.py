#!/usr/bin/env python3
"""One-off move of ASTER/GDEM buckets from this MinIO to another MinIO."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ds_download.minio_connection import MinioConnection  # noqa: E402

TARGET_MINIO_HOST_ENV = "TARGET_MINIO_HOST"
TARGET_MINIO_PORT_ENV = "TARGET_MINIO_PORT"
TARGET_MINIO_ACCESS_KEY_ENV = "TARGET_MINIO_ACCESS_KEY"
TARGET_MINIO_SECRET_KEY_ENV = "TARGET_MINIO_SECRET_KEY"
TARGET_MINIO_SECURE_ENV = "TARGET_MINIO_SECURE"
TARGET_BUCKET_NAME_ASTER_ENV = "TARGET_BUCKET_NAME_ASTER"
TARGET_BUCKET_NAME_DEM_ENV = "TARGET_BUCKET_NAME_DEM"


def fail_on_todo(name: str, value: str | bool) -> None:
    if isinstance(value, str) and (not value or value.startswith("TODO_")):
        raise SystemExit(f"Set {name} at the top of this script before running.")


def checked_target() -> Minio:
    host = os.environ.get(TARGET_MINIO_HOST_ENV)
    port = os.environ.get(TARGET_MINIO_PORT_ENV)
    access_key = os.environ.get(TARGET_MINIO_ACCESS_KEY_ENV)
    secret_key = os.environ.get(TARGET_MINIO_SECRET_KEY_ENV)
    secure = os.environ.get(TARGET_MINIO_SECURE_ENV, "false").lower() == "true"
    aster_bucket = os.environ.get(TARGET_BUCKET_NAME_ASTER_ENV)
    dem_bucket = os.environ.get(TARGET_BUCKET_NAME_DEM_ENV)

    for name, value in {
        TARGET_MINIO_HOST_ENV: host,
        TARGET_MINIO_PORT_ENV: port,
        TARGET_MINIO_ACCESS_KEY_ENV: access_key,
        TARGET_MINIO_SECRET_KEY_ENV: secret_key,
        TARGET_BUCKET_NAME_ASTER_ENV: aster_bucket,
        TARGET_BUCKET_NAME_DEM_ENV: dem_bucket,
    }.items():
        fail_on_todo(name, value)

    return Minio(
        endpoint=f"{host}:{port}",
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


def already_copied(target: Minio, bucket_name: str, object_name: str, size: int) -> bool:
    try:
        return target.stat_object(bucket_name, object_name).size == size
    except S3Error as exc:
        if exc.code in {"NoSuchBucket", "NoSuchKey", "NoSuchObject"}:
            return False
        raise


def move_bucket(
    source: MinioConnection,
    source_bucket: str,
    target: Minio,
    target_bucket: str,
    dry_run: bool,
) -> None:
    target_bucket_exists = target.bucket_exists(target_bucket)
    if not target_bucket_exists:
        print(f"create bucket {target_bucket}")
        if not dry_run:
            target.make_bucket(target_bucket)
            target_bucket_exists = True

    for obj in source.list_objects(source_bucket, recursive=True):
        if target_bucket_exists and already_copied(target, target_bucket, obj.object_name, obj.size):
            print(f"skip copied {target_bucket}/{obj.object_name}")
            if not dry_run:
                source.remove_object(source_bucket, obj.object_name)
            continue

        print(f"{source_bucket}/{obj.object_name} -> {target_bucket}/{obj.object_name}")
        if dry_run:
            continue

        data = source.get_object(source_bucket, obj.object_name)
        try:
            target.put_object(target_bucket, obj.object_name, data, obj.size)
        finally:
            data.close()
            data.release_conn()
        if not already_copied(target, target_bucket, obj.object_name, obj.size):
            raise RuntimeError(f"Uploaded size mismatch: {target_bucket}/{obj.object_name}")
        source.remove_object(source_bucket, obj.object_name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        fail_on_todo("TARGET_MINIO_HOST", "real-host")
        stat = type("Stat", (), {"size": 123})()
        target = type("Target", (), {"stat_object": lambda *_: stat})()
        assert already_copied(target, "bucket", "object.tif", 123)
        assert not already_copied(target, "bucket", "object.tif", 456)
        try:
            fail_on_todo("TARGET_MINIO_HOST", "TODO_TARGET_HOST")
        except SystemExit:
            return
        raise AssertionError("TODO target config should fail")

    load_dotenv(ROOT / ".env")
    source = MinioConnection()
    target = checked_target()

    target_aster_bucket = os.environ.get(TARGET_BUCKET_NAME_ASTER_ENV)
    target_dem_bucket = os.environ.get(TARGET_BUCKET_NAME_DEM_ENV)
    for source_bucket, target_bucket in (
        (os.environ.get("MINIO_BUCKET_NAME_ASTER"), target_aster_bucket),
        (os.environ.get("MINIO_BUCKET_NAME_DEM"), target_dem_bucket),
    ):
        if not source_bucket:
            raise SystemExit("Set MINIO_BUCKET_NAME_ASTER and MINIO_BUCKET_NAME_DEM in .env.")
        move_bucket(source, source_bucket, target, target_bucket, args.dry_run)


if __name__ == "__main__":
    main()
