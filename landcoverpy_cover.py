import datetime
import traceback
from itertools import compress
from pathlib import Path
from typing import Iterable, Tuple

import numpy as np
import rasterio
from landcoverpy import raw_index_calculation_composite
from landcoverpy.composite import (_composite, _expand_cloud_mask,
                                   _get_id_composite, _get_title_composite)
from landcoverpy.config import settings
from landcoverpy.execution_mode import ExecutionMode
from landcoverpy.minio import MinioConnection
from landcoverpy.utilities.raster import (_get_kwargs_raster,
                                          _get_raster_filename_from_path,
                                          _get_raster_name_from_path,
                                          _get_spatial_resolution_raster,
                                          _read_raster)
from landcoverpy.utilities.sentinel import _sentinel_date_to_datetime
from pymongo.collection import Collection
from pymongo.cursor import Cursor

def get_products_by_tile_and_date(
    tile: str,
    mongo_collection: Collection,
    start_date: datetime,
    end_date: datetime,
    cloud_percentage,
) -> Cursor:
    """
    Query to mongo for obtaining products filtered by tile, date and cloud percentage
    """
    product_metadata_cursor = mongo_collection.aggregate(
        [
            {
                "$project": {
                    "_id": 1,
                    "intermediateProducts": {
                        "$filter": {
                            "input": "$intermediateProducts",
                            "as": "intermediateProducts",
                            "cond": {
                                "$and": [
                                    {"$eq": ["$$intermediateProducts.mask", None]},
                                    {"$eq": ["$$intermediateProducts.name", "cloud-mask"]},
                                    {"$lt": ["$$intermediateProducts.value", cloud_percentage/100]},
                                ]
                            },
                        }
                    },
                    "id": 1,
                    "title": 1,
                    "size": 1,
                    "date": 1,
                    "creationDate": 1,
                    "ingestionDate": 1,
                    "objectName": 1,
                }
            },
            {
                "$match": {
                    "intermediateProducts.0": {"$exists": True},
                    "title": {"$regex": f"_T{tile}_"},
                    "date": {
                        "$gte": start_date,
                        "$lte": end_date,
                    },
                }
            },
            {
                "$sort": {"date":-1}
            },
        ]
    )

    return product_metadata_cursor

def get_product_rasters_paths(
    product_metadata: dict, minio_client: MinioConnection, minio_bucket: str
) -> Tuple[Iterable[str], Iterable[bool]]:
    """
    Get the paths to all rasters of a product in minio.
    Another list of boolean is returned, it points out if each raster is a sentinel band or not (e.g. index).
    """
    product_title = product_metadata["title"]
    product_dir = None

    if minio_bucket == settings.MINIO_BUCKET_NAME_PRODUCTS:
        tile = product_metadata["title"].split("_T")[1][0:5]
        year = product_metadata["date"].strftime("%Y")
        month = product_metadata["date"].strftime("%B")
        product_dir = f"{tile}/{year}/{month}/products/{product_title}"

    elif minio_bucket == settings.MINIO_BUCKET_NAME_COMPOSITES:
        product_dir = product_title

    bands_dir = f"{product_dir}/raw/"
    indexes_dir = f"{product_dir}/indexes/"

    bands_paths = minio_client.list_objects(minio_bucket, prefix=bands_dir)
    indexes_path = minio_client.list_objects(minio_bucket, prefix=indexes_dir)

    rasters = []
    is_band = []
    for index_path in indexes_path:
        rasters.append(index_path.object_name)
        is_band.append(False)
    for band_path in bands_paths:
        rasters.append(band_path.object_name)
        is_band.append(True)

    return (rasters, is_band)

def create_composite(
    products_metadata: Iterable[dict],
    minio_client: MinioConnection,
    bucket_products: str,
    bucket_composites: str,
    mongo_composites_collection: Collection,
    execution_mode: ExecutionMode
) -> None:
    """
    Compose multiple Sentinel-2 products into a new product, this product is called "composite".
    Each band of the composite is computed using the pixel-wise median. Cloudy pixels of each product are not used in the median.
    Cloud masks are expanded if the execution mode is training, creating a composite with "S2E" prefix.
    If execution mode is predict, Sentinel's default cloud masks are used, creating a composite with "S2S" prefix.
    Once computed, the composite is stored in Minio, and its metadata in Mongo. 
    """

    products_titles = [product["title"] for product in products_metadata]
    print(
        "Creating composite of ", len(products_titles), " products: ", products_titles
    )

    products_ids = []
    products_titles = []
    products_dates = []
    products_tiles = []
    bands_paths_products = []
    cloud_masks_temp_paths = []
    cloud_masks = {"10": [], "20": [], "60": []}
    scl_cloud_values = [3, 8, 9, 10, 11]

    print("Downloading and reading the cloud masks needed to make the composite")
    for product_metadata in products_metadata:

        product_title = product_metadata["title"]
        products_ids.append(product_metadata["id"])
        products_titles.append(product_title)
        products_dates.append(product_title.split("_")[2])
        products_tiles.append(product_title.split("_")[5])

        (rasters_paths, is_band) = get_product_rasters_paths(
            product_metadata, minio_client, bucket_products
        )
        bands_paths_product = list(compress(rasters_paths, is_band))
        bands_paths_products.append(bands_paths_product)

        # Download cloud masks in all different spatial resolutions
        for band_path in bands_paths_product:
            band_name = _get_raster_name_from_path(band_path)
            band_filename = _get_raster_filename_from_path(band_path)
            if "SCL" in band_name:
                temp_dir_product = f"{settings.TMP_DIR}/{product_title}.SAFE"
                temp_path_product_band = f"{temp_dir_product}/{band_filename}"
                minio_client.fget_object(bucket_products, band_path, str(temp_path_product_band))
                cloud_masks_temp_paths.append(temp_path_product_band)
                spatial_resolution = str(
                    int(_get_spatial_resolution_raster(temp_path_product_band))
                )
                # Adri: Arreglado hasta aquí ...
                scl_band = _read_raster(temp_path_product_band)
                # Binarize scl band to get a cloud mask
                cloud_mask = np.isin(scl_band, scl_cloud_values).astype(np.bool)
                # Expand cloud mask for a more aggresive masking
                if execution_mode == ExecutionMode.TRAINING:
                    cloud_mask = _expand_cloud_mask(cloud_mask, int(spatial_resolution))
                cloud_masks[spatial_resolution].append(cloud_mask)
                kwargs = _get_kwargs_raster(temp_path_product_band)
                with rasterio.open(temp_path_product_band, "w", **kwargs) as f:
                    f.write(cloud_mask)

                # 10m spatial resolution cloud mask raster does not exists, have to be rescaled from 20m mask
                if "SCL_20m" in band_filename:
                    scl_band_10m_temp_path = temp_path_product_band.replace(
                        "_20m.jp2", "_10m.jp2"
                    )
                    cloud_mask_10m = _read_raster(
                        temp_path_product_band,
                        rescale=True,
                        path_to_disk=scl_band_10m_temp_path,
                        to_tif=False,
                    )
                    cloud_masks_temp_paths.append(scl_band_10m_temp_path)
                    cloud_masks["10"].append(cloud_mask_10m)

    composite_id = _get_id_composite(products_ids, execution_mode)
    composite_title = _get_title_composite(products_dates, products_tiles, composite_id, execution_mode)
    temp_path_composite = Path(settings.TMP_DIR, composite_title + ".SAFE")

    uploaded_composite_band_paths = []
    temp_paths_composite_bands = []
    temp_product_dirs = []
    result = None
    try:
        num_bands = len(bands_paths_products[0])
        for i_band in range(num_bands):
            products_i_band_path = [
                bands_paths_product[i_band]
                for bands_paths_product in bands_paths_products
            ]
            band_name = _get_raster_name_from_path(products_i_band_path[0])
            band_filename = _get_raster_filename_from_path(products_i_band_path[0])
            if "SCL" in band_name:
                continue
            temp_path_composite_band = Path(temp_path_composite, band_filename)

            temp_path_list = []

            for product_i_band_path in products_i_band_path:
                product_title = product_i_band_path.split("/")[2]

                temp_dir_product = f"{settings.TMP_DIR}/{product_title}.SAFE"
                temp_path_product_band = f"{temp_dir_product}/{band_filename}"
                print(
                    f"Downloading raster {band_name} from minio into {temp_path_product_band}"
                )
                minio_client.fget_object(bucket_products, product_i_band_path, str(temp_path_product_band))
                spatial_resolution = str(
                    int(_get_spatial_resolution_raster(temp_path_product_band))
                )

                if temp_dir_product not in temp_product_dirs:
                    temp_product_dirs.append(temp_dir_product)
                temp_path_list.append(temp_path_product_band)
            composite_i_band, kwargs_composite = _composite(
                temp_path_list,
                method="median",
                cloud_masks=cloud_masks[spatial_resolution],
            )

            # Save raster to disk
            if not Path.is_dir(temp_path_composite):
                Path.mkdir(temp_path_composite)

            temp_path_composite_band = str(temp_path_composite_band)
            if temp_path_composite_band.endswith(".jp2"):
                temp_path_composite_band = temp_path_composite_band[:-3] + "tif"

            temp_path_composite_band = Path(temp_path_composite_band)

            temp_paths_composite_bands.append(temp_path_composite_band)

            with rasterio.open(
                temp_path_composite_band, "w", **kwargs_composite
            ) as file_composite:
                file_composite.write(composite_i_band)

            # Upload raster to minio
            band_filename = band_filename[:-3] + "tif"
            minio_band_path = f"{composite_title}/raw/{band_filename}"
            minio_client.fput_object(
                bucket_name=bucket_composites,
                object_name=minio_band_path,
                file_path=temp_path_composite_band,
                content_type="image/tif",
            )
            uploaded_composite_band_paths.append(minio_band_path)
            print(
                f"Uploaded raster: -> {temp_path_composite_band} into {bucket_composites}:{minio_band_path}"
            )

        composite_metadata = dict()
        composite_metadata["id"] = composite_id
        composite_metadata["title"] = composite_title
        composite_metadata["products"] = [
            dict(id=product_id, title=products_title)
            for (product_id, products_title) in zip(products_ids, products_titles)
        ]
        composite_metadata["first_date"] = _sentinel_date_to_datetime(
            min(products_dates)
        )
        composite_metadata["last_date"] = _sentinel_date_to_datetime(
            max(products_dates)
        )

        # Upload metadata to mongo
        result = mongo_composites_collection.insert_one(composite_metadata)
        print("Inserted data in mongo, id: ", result.inserted_id)

        # Compute indexes
        raw_index_calculation_composite.calculate_raw_indexes(composite_id)

    except (Exception, KeyboardInterrupt) as e:
        print("Removing uncompleted composite from minio")
        traceback.print_exc()
        for composite_band in uploaded_composite_band_paths:
            minio_client.remove_object(
                bucket_name=bucket_composites, object_name=composite_band
            )
        products_ids = [
            products_metadata["id"] for products_metadata in products_metadata
        ]
        mongo_composites_collection.delete_one({"id": _get_id_composite(products_ids)})
        raise e

    finally:
        for composite_band in temp_paths_composite_bands + cloud_masks_temp_paths:
            Path.unlink(Path(composite_band))