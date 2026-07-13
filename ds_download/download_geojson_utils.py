
import structlog

from datetime import datetime
from hashlib import sha256
import re
from os.path import join
from pathlib import Path

from ds_download.download_using_sentinel_api import find_products_sentinel_api_by_geojson_file
from ds_download.minio_connection import MinioConnection
from main_script_europe import month_range, product_title_date

logger = structlog.get_logger(__file__)

def normalize_geojson_name(geojson_path: str) -> str:
    geojson_name = Path(geojson_path).stem
    return re.sub(r"[^A-Za-z0-9_-]+", "_", geojson_name)


def str_to_bool(value: str) -> bool:
    """Convert a string command line argument to a boolean."""
    if isinstance(value, bool):
        return value
    value_lower = value.strip().lower()
    if value_lower in {"true", "t", "yes", "y", "1"}:
        return True
    if value_lower in {"false", "f", "no", "n", "0"}:
        return False
    raise ValueError(f"Boolean value expected for quantize, got '{value}'")


def geojson_composite_title(geojson_name: str, product_titles: list[str]) -> str | None:
    if not product_titles:
        return None

    product_dates = [title.split("_")[2].split("T")[0] for title in product_titles]
    title_hash = sha256("".join(sorted(product_titles)).encode("utf-8")).hexdigest()[:8]
    normalized_name = re.sub(r"[^A-Za-z0-9_-]+", "_", geojson_name)
    return f"S2S_MSIL2A_{min(product_dates)}_NXXX_RXXX_{normalized_name}_{max(product_dates)}_{title_hash}"


def geojson_monthly_index_key(
    geojson_name: str,
    year: int,
    month: int,
    product_titles: list[str],
    index_name: str,
) -> str | None:
    title = geojson_composite_title(geojson_name, product_titles)
    if title is None:
        return None

    month_name = datetime(year, month, 1).strftime("%B")
    return join(
        "geojson",
        geojson_name,
        str(year),
        month_name,
        "composites",
        title,
        "indexes",
        f"{index_name.lower()}.tif",
    )


def geojson_monthly_composite_raw_prefix(
    geojson_name: str,
    year: int,
    month: int,
    title: str,
) -> str:
    month_name = datetime(year, month, 1).strftime("%B")
    return join("geojson", geojson_name, str(year), month_name, "composites", title, "raw", "")


def build_geojson_monthly_composite_metadata(
    geojson_name: str,
    year: int,
    month: int,
    product_titles: list[str],
    bucket_name: str,
) -> dict | None:
    title = geojson_composite_title(geojson_name, product_titles)
    if title is None:
        return None

    products_dates = [product_title_date(product_title) for product_title in product_titles]
    return {
        "title": title,
        "products": [{"title": product_title} for product_title in product_titles],
        "first_date": min(products_dates),
        "last_date": max(products_dates),
        "S3Bucket": bucket_name,
        "S3BandsPrefix": geojson_monthly_composite_raw_prefix(
            geojson_name,
            year,
            month,
            title,
        ),
        "tile": f"geojson/{geojson_name}",
    }


def product_filter_for_tiles(tile_ids: list[str], start_date: datetime, end_date: datetime) -> dict:
    return {
        "tile": {"$in": tile_ids},
        "datetakeSensingTime": {"$gte": start_date, "$lt": end_date},
    }


def get_geojson_tiles_for_month(
    geojson_path: str,
    year: int,
    month: int,
) -> list[str]:
    start_date, end_date = month_range(year, month)
    response = find_products_sentinel_api_by_geojson_file(
        geojson_path,
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
    )
    tiles = set()
    for product in response:
        product_title = product["Name"].replace(".SAFE", "")
        tile_id = product_title.split("_T")[1][0:5]
        tiles.add(tile_id)
    return sorted(tiles)


def get_geojson_monthly_index_products(
    geojson_path: str,
    year: int,
    month: int,
    index_name: str,
    mongo_col,
) -> list[dict]:
    start_date, end_date = month_range(year, month)
    tiles = get_geojson_tiles_for_month(geojson_path, year, month)
    if not tiles:
        return []

    raster_key = f"indexes.{index_name.lower()}.rasterS3Key"
    return list(mongo_col.find(
        {
            **product_filter_for_tiles(tiles, start_date, end_date),
            raster_key: {"$exists": True},
        },
        {"title": 1, raster_key: 1},
    ))


def cleanup_geojson_product_data(
    geojson_path: str,
    year: int,
    month: int,
    mongo_col,
    minio_client: MinioConnection,
) -> None:
    start_date, end_date = month_range(year, month)
    tiles = get_geojson_tiles_for_month(geojson_path, year, month)
    if not tiles:
        return

    query = product_filter_for_tiles(tiles, start_date, end_date)
    products = list(mongo_col.find(query, {"title": 1}))

    for product in products:
        title = product["title"]
        try:
            tile_id = title.split("_T")[1][0:5]
            year_str = title.split("_")[2][0:4]
            month_name = datetime.strptime(title.split("_")[2][4:6], "%m").strftime("%B")
            prefix = join(tile_id, year_str, month_name, "products", title, "")
        except Exception as exc:
            logger.warning(f"Could not parse product title for cleanup: {title} ({exc})")
            continue

        for obj in minio_client.list_objects(minio_client.bucket_name, prefix=prefix, recursive=True):
            minio_client.remove_object(minio_client.bucket_name, obj.object_name)

    mongo_col.delete_many(query)
