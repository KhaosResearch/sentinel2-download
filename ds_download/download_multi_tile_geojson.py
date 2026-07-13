import os
from collections import defaultdict
from datetime import datetime
from os.path import join
from pathlib import Path

import structlog
from google.cloud import storage
from rasterio.io import MemoryFile
from rasterio.merge import merge

from ds_download.download_from_google_cloud import (
    get_google_blobs_metadata,
    generate_sentinel_metadata,
    is_product_already_stored,
    mask_jp2_to_geotiff,
)
from ds_download.minio_connection import MinioConnection
from ds_download.mongo_connection import MongoConnection
from ds_download.raw_index_calculation import calculate_raw_index

logger = structlog.get_logger(__file__)

from rasterio.merge import merge

def process_multi_tile_geojson(blob_list, geojson_path, product_title, tmp_dir):
    """
    1. Streams multiple blobs into memory.
    2. Merges them into a single continuous raster grid.
    3. Masks the result to the GeoJSON.
    4. Writes the final GeoTIFF.
    """
    # Dictionary to hold the opened MemoryFiles for all tiles
    mem_files = [MemoryFile(blob.download_as_bytes()) for blob in blob_list]
    src_datasets = [mf.open() for mf in mem_files]
    
    try:
        # Merge all tiles into one seamless mosaic in memory
        mosaic, out_trans = merge(src_datasets)
        
        # Create a temporary virtual dataset for the mosaic to use as a source
        with MemoryFile() as mosaic_memfile:
            with mosaic_memfile.open(
                driver='GTiff', 
                height=mosaic.shape[1], 
                width=mosaic.shape[2], 
                count=mosaic.shape[0], 
                dtype=mosaic.dtype, 
                transform=out_trans, 
                crs=src_datasets[0].crs
            ) as mosaic_dst:
                mosaic_dst.write(mosaic)
                
                # Now pass this seamless mosaic to your existing masking logic
                output_path = Path(tmp_dir) / product_title / "merged_output.tif"
                success = mask_jp2_to_geotiff(mosaic_memfile.read(), geojson_path, output_path)
                return output_path if success else None
    finally:
        for src in src_datasets: src.close()
        for mf in mem_files: mf.close()
        
def _normalize_product_title(name: str) -> str:
    return name.replace(".SAFE", "")


def _group_response_by_date(filtered_response: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in filtered_response:
        title = item.get("Name") or item.get("name")
        if not title:
            continue
        product_title = _normalize_product_title(title)
        date_value = item.get("ContentDate", {}).get("Start")
        if not date_value:
            continue
        date_str = str(date_value)[:10]
        groups[date_str].append(item)
    return groups


def _build_multi_tile_product_title(product_titles: list[str]) -> str:
    sorted_titles = sorted(product_titles)
    base_title = sorted_titles[0]
    tile_ids = sorted({title.split("_T")[1][0:5] for title in sorted_titles if "_T" in title})
    if len(tile_ids) <= 1:
        return base_title
    suffix = "-".join(tile_ids)
    return f"{base_title}_MULTI-{suffix}"


def _download_band_blobs_for_group(
    product_titles: list[str],
    required_bands: list[str],
    storage_client: storage.Client,
    bucket_name: str,
) -> dict[str, list]:
    band_blobs: dict[str, list] = {band: [] for band in required_bands}

    for product_title in product_titles:
        blobs_metadata = get_google_blobs_metadata(product_title, bucket_name, storage_client)
        if blobs_metadata is None:
            continue
        blobs, _, _ = blobs_metadata
        for blob in blobs:
            if blob.name.endswith("/") or "IMG_DATA" not in blob.name:
                continue
            for band in required_bands:
                if f"_{band}." in blob.name:
                    band_blobs[band].append(blob)
    return band_blobs


def process_multi_tile_geojson(
    blob_list: list,
    geojson_path: str,
    output_path: Path,
    mosaic_output_path: Path | None = None,
) -> Path | None:
    """
    Merge same-band JP2 tiles into an in-memory raster and mask the resulting virtual mosaic to the GeoJSON AOI.
    """
    mem_files = [MemoryFile(blob.download_as_bytes()) for blob in blob_list]
    src_datasets = [mf.open() for mf in mem_files]

    try:
        mosaic, out_trans = merge(src_datasets)
        with MemoryFile() as mosaic_memfile:
            with mosaic_memfile.open(
                driver="GTiff",
                height=mosaic.shape[1],
                width=mosaic.shape[2],
                count=mosaic.shape[0],
                dtype=mosaic.dtype,
                transform=out_trans,
                crs=src_datasets[0].crs,
            ) as mosaic_dst:
                mosaic_dst.write(mosaic)

            mosaic_memfile.seek(0)
            mosaic_bytes = mosaic_memfile.read()

            if mosaic_output_path is not None:
                mosaic_output_path.parent.mkdir(parents=True, exist_ok=True)
                mosaic_output_path.write_bytes(mosaic_bytes)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            success = mask_jp2_to_geotiff(mosaic_bytes, geojson_path, output_path)
            return output_path if success else None
    finally:
        for src in src_datasets:
            src.close()
        for mf in mem_files:
            mf.close()


def _upload_to_minio(
    minio_client: MinioConnection,
    local_path: Path,
    minio_key: str,
    content_type: str,
) -> None:
    minio_client.fput_object(
        minio_client.bucket_name,
        minio_key,
        str(local_path),
        content_type=content_type,
    )


def download_multi_tile_geojson(
    calculate_raw_indexes: bool,
    calculate_intermediate_products: bool,
    filtered_response: list[dict],
    geojson_path: str,
    sentinel_metadata: dict | None = None,
    required_bands: list[str] | None = None,
    quantize: bool = True,
) -> None:
    """
    Stream multi-tile Sentinel-2 GeoJSON downloads from Google Cloud, build a virtual mosaic per date,
    apply GeoJSON masking, upload the masked raster bands to MinIO, insert Mongo metadata, and calculate indexes.
    """
    if not filtered_response:
        logger.warning("No filtered products provided for multi-tile GeoJSON download.")
        return

    tmp_dir = os.environ.get("TMP_DIR")
    gcloud_bucket_name = os.environ.get("GOOGLE_CLOUD_BUCKET_NAME")
    if not tmp_dir or not gcloud_bucket_name:
        raise EnvironmentError("TMP_DIR and GOOGLE_CLOUD_BUCKET_NAME must be set for multi-tile GeoJSON downloads.")

    if required_bands is None:
        raise ValueError("required_bands is required for multi-tile GeoJSON processing.")

    storage_client = storage.Client()
    mongo_col = MongoConnection().get_collection_object()
    minio_client = MinioConnection()
    date_groups = _group_response_by_date(filtered_response)

    for date_str, products in date_groups.items():
        product_titles = [_normalize_product_title(item.get("Name") or item.get("name")) for item in products]
        product_title = _build_multi_tile_product_title(product_titles)

        minio_dir, product_mongo_data, minio_found = is_product_already_stored(product_title, mongo_col, minio_client)
        if product_mongo_data and minio_found:
            logger.debug(f"Multi-tile product already stored: {product_title}")
            continue

        band_blobs = _download_band_blobs_for_group(product_titles, required_bands, storage_client, gcloud_bucket_name)
        missing_bands = [band for band, blobs in band_blobs.items() if not blobs]
        if missing_bands:
            logger.warning(
                f"Date {date_str}: missing required bands {missing_bands} for multi-tile group {product_titles}. Skipping."
            )
            continue

        local_product_dir = Path(tmp_dir) / product_title
        local_product_dir.mkdir(parents=True, exist_ok=True)

        intermediate_prefix = join(minio_dir, product_title, "virtual_mosaic", "")
        raw_prefix = join(minio_dir, product_title, "raw", "")
        uploaded_count = 0

        for band, blobs in band_blobs.items():
            local_band_path = local_product_dir / f"{band}.tif"
            mosaic_band_path = local_product_dir / "virtual_mosaic" / f"{band}.tif"
            output_path = process_multi_tile_geojson(blobs, geojson_path, local_band_path, mosaic_band_path)
            if output_path is None:
                logger.warning(f"Failed to mask band {band} for {product_title}.")
                break

            _upload_to_minio(minio_client, output_path, join(raw_prefix, output_path.name), "image/tif")
            _upload_to_minio(minio_client, mosaic_band_path, join(intermediate_prefix, mosaic_band_path.name), "image/tif")
            uploaded_count += 1

        if uploaded_count != len(required_bands):
            logger.warning(f"Multi-tile GeoJSON date {date_str} did not produce all required bands. Cleaning local state.")
            try:
                for child in sorted(local_product_dir.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
            except OSError as exc:
                logger.warning(f"Unable to clean local files for {product_title}: {exc}")
            continue

        blobs_metadata = get_google_blobs_metadata(product_titles[0], gcloud_bucket_name, storage_client)
        if blobs_metadata is None:
            logger.warning(f"Could not resolve Google Cloud source metadata for {product_titles[0]}. Skipping.")
            continue
        source_blob_name = blobs_metadata[1]
        metadata = generate_sentinel_metadata(
            sentinel_metadata or {},
            gcloud_bucket_name,
            source_blob_name,
            product_title,
            minio_client,
            minio_dir,
            join(tmp_dir, product_title, ""),
            is_geojson=True,
        )

        try:
            mongo_col.insert_one(metadata)
        except Exception as exc:
            logger.warning(f"Failed to insert Mongo metadata for {product_title}: {exc}")

        if calculate_intermediate_products:
            calculate_raw_index(
                product_title=product_title,
                index=["CloudMask"],
                minio_folder_name="intermediateProducts",
            )

        if calculate_raw_indexes:
            calculate_raw_index(
                product_title=product_title,
                index=["NDVI", "NDWI"],
                quantize=quantize,
            )

        try:
            for child in sorted(local_product_dir.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
        except OSError as exc:
            logger.warning(f"Unable to clean local multi-tile temp files for {product_title}: {exc}")
