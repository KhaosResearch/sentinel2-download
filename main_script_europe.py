from argparse import ArgumentParser
from datetime import datetime
from hashlib import sha256
import os
from os.path import join
from pathlib import Path
import shutil

import geopandas as gpd
import numpy as np
import rasterio
import requests
from dotenv import load_dotenv
from shapely.geometry import box

from ds_download.download_using_sentinel_api import download_product_using_sentinel_api, find_products_sentinel_api_by_tile_id
from ds_download.minio_connection import MinioConnection
from ds_download.mongo_connection import MongoConnection
from ds_download.raw_index_calculation import INT16_NODATA, calculate_raw_index, compress_and_quantize_tiff

GRID_URL = "https://zenodo.org/records/10998972/files/sentinel2_tiling_grid_wgs84.geojson?download=1"
COUNTRIES_URL = "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip"
GRID_CACHE = Path("data/sentinel2_tiling_grid_wgs84.geojson")
COUNTRIES_CACHE = Path("data/ne_10m_admin_0_countries.zip")
TILE_CACHE = Path("data/sentinel2_europe_land_tiles.txt")
NDWI_BANDS = ["B03_10m", "B08_10m"]
MONTHS = range(1, 13)
OLD_MONTHLY_NDWI_FILENAME = "NDWI_mean.tif"
MONTHLY_NDWI_FILENAME = "ndwi.tif"


def parse_args():
    parser = ArgumentParser(description="Download Sentinel-2 products for Europe land tiles and calculate NDWI.")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--refresh-tiles", action="store_true")
    return parser.parse_args()


def download_file(url: str, output_path: Path, refresh: bool = False) -> Path:
    if output_path.exists() and not refresh:
        return output_path

    output_path.parent.mkdir(exist_ok=True)
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path


def get_europe_land_tiles(refresh: bool = False) -> list[str]:
    if TILE_CACHE.exists() and not refresh:
        return [line.strip() for line in TILE_CACHE.read_text().splitlines() if line.strip()]

    grid_path = download_file(GRID_URL, GRID_CACHE, refresh=refresh)
    countries_path = download_file(COUNTRIES_URL, COUNTRIES_CACHE, refresh=refresh)

    grid = gpd.read_file(grid_path)
    countries = gpd.read_file(countries_path).to_crs(grid.crs)

    # Geographic Europe approximation, clipped to avoid transcontinental country geometries
    # selecting tiles far outside Europe.
    europe_bbox = box(-25, 34, 60, 72)
    europe_geometries = countries[countries["CONTINENT"] == "Europe"].intersection(europe_bbox)
    if hasattr(europe_geometries, "union_all"):
        europe_land = europe_geometries.union_all()
    else:
        europe_land = europe_geometries.unary_union

    tile_column = next(
        col
        for col in grid.columns
        if col.lower() in {"name", "tile", "tile_id", "mgrs", "mgrs_tile", "mgrs_tile_id"}
    )
    europe_tiles = grid[grid.intersects(europe_land)]
    tiles = sorted(europe_tiles[tile_column].astype(str).str.replace("^T", "", regex=True))

    TILE_CACHE.parent.mkdir(exist_ok=True)
    TILE_CACHE.write_text("\n".join(tiles) + "\n")
    return tiles


def product_filter(tile: str, start_date: datetime, end_date: datetime) -> dict:
    return {
        "tile": tile,
        "datetakeSensingTime": {"$gte": start_date, "$lt": end_date},
    }


def year_range(year: int) -> tuple[datetime, datetime]:
    return datetime(year, 1, 1), datetime(year + 1, 1, 1)


def month_range(year: int, month: int) -> tuple[datetime, datetime]:
    start_date = datetime(year, month, 1)
    end_date = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start_date, end_date


def old_monthly_ndwi_key(tile: str, year: int, month: int) -> str:
    month_name = datetime(year, month, 1).strftime("%B")
    return join(tile, str(year), month_name, "monthly", OLD_MONTHLY_NDWI_FILENAME)


def monthly_ndwi_prefix(tile: str, year: int, month: int) -> str:
    month_name = datetime(year, month, 1).strftime("%B")
    return join(tile, str(year), month_name, "composites", "")


def composite_title(tile: str, product_titles: list[str]) -> str | None:
    if not product_titles:
        return None

    product_dates = [title.split("_")[2].split("T")[0] for title in product_titles]
    title_hash = sha256("".join(sorted(product_titles)).encode("utf-8")).hexdigest()[:8]
    return f"S2S_MSIL2A_{min(product_dates)}_NXXX_RXXX_T{tile}_{max(product_dates)}_{title_hash}"


def monthly_ndwi_key(tile: str, year: int, month: int, product_titles: list[str]) -> str | None:
    title = composite_title(tile, product_titles)
    if title is None:
        return None

    month_name = datetime(year, month, 1).strftime("%B")
    return join(tile, str(year), month_name, "composites", title, "indexes", MONTHLY_NDWI_FILENAME)


def object_exists(minio_client: MinioConnection, object_name: str) -> bool:
    try:
        minio_client.stat_object(minio_client.bucket_name, object_name)
        return True
    except Exception:
        return False


def find_existing_monthly_ndwi_key(minio_client: MinioConnection, tile: str, year: int, month: int) -> str | None:
    prefix = monthly_ndwi_prefix(tile, year, month)
    for obj in minio_client.list_objects(minio_client.bucket_name, prefix=prefix, recursive=True):
        if obj.object_name.endswith(join("indexes", MONTHLY_NDWI_FILENAME)):
            return obj.object_name
    return None


def get_month_product_titles(tile: str, year: int, month: int, mongo_col) -> list[str]:
    start_date, end_date = month_range(year, month)
    products = list(mongo_col.find(product_filter(tile, start_date, end_date), {"title": 1}))
    if products:
        return [product["title"] for product in products]

    response = find_products_sentinel_api_by_tile_id(
        tile,
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
    )
    return [product["Name"].replace(".SAFE", "") for product in response]


def migrate_old_monthly_output(
    tile: str,
    year: int,
    month: int,
    mongo_col,
    minio_client: MinioConnection,
    tmp_dir: Path,
) -> str | None:
    existing_key = find_existing_monthly_ndwi_key(minio_client, tile, year, month)
    if existing_key:
        return existing_key

    old_key = old_monthly_ndwi_key(tile, year, month)
    product_titles = get_month_product_titles(tile, year, month, mongo_col)
    new_key = monthly_ndwi_key(tile, year, month, product_titles)

    if not new_key or not object_exists(minio_client, old_key):
        return None

    local_path = tmp_dir / tile / str(year) / f"{month:02}" / OLD_MONTHLY_NDWI_FILENAME
    local_path.parent.mkdir(parents=True, exist_ok=True)
    minio_client.fget_object(minio_client.bucket_name, old_key, str(local_path))
    minio_client.fput_object(minio_client.bucket_name, new_key, local_path, content_type="image/tif")
    minio_client.remove_object(minio_client.bucket_name, old_key)
    print(f"Migrated monthly NDWI mean: {old_key} -> {new_key}")
    return new_key


def missing_months(tile: str, year: int, mongo_col, minio_client: MinioConnection, tmp_dir: Path) -> list[int]:
    missing = []
    for month in MONTHS:
        object_name = migrate_old_monthly_output(tile, year, month, mongo_col, minio_client, tmp_dir)
        if object_name:
            print(f"Skipping existing monthly NDWI mean: {object_name}")
        else:
            missing.append(month)
    return missing


def all_monthly_outputs_exist(tile: str, year: int, minio_client: MinioConnection) -> bool:
    for month in MONTHS:
        if not find_existing_monthly_ndwi_key(minio_client, tile, year, month):
            return False
    return True


def get_product_titles(tile: str, start_date: datetime, end_date: datetime, mongo_col) -> list[str]:
    products = mongo_col.find(product_filter(tile, start_date, end_date), {"title": 1})
    return [product["title"] for product in products]


def read_ndwi_from_minio(minio_client: MinioConnection, object_name: str, local_path: Path) -> tuple[np.ndarray, dict]:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    minio_client.fget_object(minio_client.bucket_name, object_name, str(local_path))
    with rasterio.open(local_path) as src:
        ndwi = src.read(1).astype(np.float32)
        metadata = src.meta.copy()
        nodata = src.nodata
        is_scaled_int = np.issubdtype(src.dtypes[0], np.integer)

    if nodata is not None:
        ndwi[ndwi == nodata] = np.nan
    ndwi[ndwi == INT16_NODATA] = np.nan
    if is_scaled_int:
        ndwi = ndwi / 10000
    return ndwi, metadata


def get_monthly_ndwi_products(tile: str, year: int, month: int, mongo_col) -> list[dict]:
    start_date, end_date = month_range(year, month)
    return list(mongo_col.find(
        {
            **product_filter(tile, start_date, end_date),
            "indexes.ndwi.rasterS3Key": {"$exists": True},
        },
        {"title": 1, "indexes.ndwi.rasterS3Key": 1},
    ))


def create_monthly_ndwi_mean(
    tile: str,
    year: int,
    month: int,
    mongo_col,
    minio_client: MinioConnection,
    tmp_dir: Path,
) -> str | None:
    start_date, _ = month_range(year, month)
    products = get_monthly_ndwi_products(tile, year, month, mongo_col)
    if not products:
        return None

    ndwi_arrays = []
    output_metadata = None
    for product in products:
        ndwi_key = product["indexes"]["ndwi"]["rasterS3Key"]
        local_path = tmp_dir / tile / str(year) / f"{month:02}" / f"{product['title']}_ndwi.tif"
        ndwi, metadata = read_ndwi_from_minio(minio_client, ndwi_key, local_path)
        if output_metadata is None:
            output_metadata = metadata
        ndwi_arrays.append(ndwi)

    monthly_ndwi = np.nanmean(np.stack(ndwi_arrays), axis=0).astype(np.float32)
    output_metadata.update(driver="GTiff", dtype=rasterio.float32, count=1, nodata=np.nan)

    product_titles = [product["title"] for product in products]
    object_name = monthly_ndwi_key(tile, year, month, product_titles)
    if object_name is None:
        return None

    month_name = start_date.strftime("%B")
    local_output = tmp_dir / tile / str(year) / month_name / OLD_MONTHLY_NDWI_FILENAME
    local_output.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(local_output, "w", **output_metadata) as dst:
        dst.write(monthly_ndwi, 1)

    compress_and_quantize_tiff(local_output)
    minio_client.fput_object(
        minio_client.bucket_name,
        object_name,
        local_output,
        content_type="image/tif",
    )
    return object_name


def cleanup_product_data(tile: str, year: int, mongo_col, minio_client: MinioConnection) -> None:
    start_date, end_date = year_range(year)
    products = list(mongo_col.find(product_filter(tile, start_date, end_date), {"title": 1, "datetakeSensingTime": 1}))

    for product in products:
        date = product["datetakeSensingTime"]
        prefix = join(tile, str(year), date.strftime("%B"), "products", product["title"])
        for obj in minio_client.list_objects(minio_client.bucket_name, prefix=prefix, recursive=True):
            minio_client.remove_object(minio_client.bucket_name, obj.object_name)

    mongo_col.delete_many(product_filter(tile, start_date, end_date))


def main():
    load_dotenv(".env")
    args = parse_args()
    base_tmp_dir = Path(os.environ.get("TMP_DIR", "tmp"))
    run_tmp_dir = base_tmp_dir / f"europe_{args.year}_{os.getpid()}"
    run_tmp_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TMP_DIR"] = str(run_tmp_dir)

    start_date, end_date = year_range(args.year)
    mongo_col = MongoConnection().get_collection_object()
    minio_client = MinioConnection()
    tmp_dir = run_tmp_dir / "monthly_ndwi"

    try:
        for tile in get_europe_land_tiles(refresh=args.refresh_tiles):
            months_to_process = missing_months(tile, args.year, mongo_col, minio_client, tmp_dir)
            if not months_to_process:
                cleanup_product_data(tile, args.year, mongo_col, minio_client)
                continue

            for month in months_to_process:
                month_start, month_end = month_range(args.year, month)
                print(f"Downloading products for {tile} in {month_start.strftime('%B')} {args.year}")
                download_product_using_sentinel_api(
                    calculate_raw_indexes=False,
                    calculate_intermediate_products=False,
                    from_date=month_start,
                    to_date=month_end,
                    tile_id=tile,
                    required_bands=NDWI_BANDS,
                )

            for month in months_to_process:
                month_start, month_end = month_range(args.year, month)
                for product_title in get_product_titles(tile, month_start, month_end, mongo_col):
                    print(f"Calculating NDWI for {product_title}")
                    calculate_raw_index(product_title=product_title, index=["NDWI"])

            monthly_outputs = []
            for month in months_to_process:
                output_key = create_monthly_ndwi_mean(tile, args.year, month, mongo_col, minio_client, tmp_dir)
                if output_key:
                    monthly_outputs.append(output_key)
                    print(f"Uploaded monthly NDWI mean: {output_key}")

            if monthly_outputs and all_monthly_outputs_exist(tile, args.year, minio_client):
                cleanup_product_data(tile, args.year, mongo_col, minio_client)
                shutil.rmtree(tmp_dir / tile / str(args.year), ignore_errors=True)
    finally:
        shutil.rmtree(run_tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
