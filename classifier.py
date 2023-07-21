import joblib
import os
import pyproj
import rasterio

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from minio import Minio
from shapely.geometry import Point
from shapely.ops import transform

load_dotenv()


def read_band(band_filename, dst_crs="EPSG:4326"):
    """
    Reads the band specified in 'band_filename' and extracts information by pixel,
    where each pixel value is a matrix position in the same order.
    A 100x100px image resolution would result in a 100x100 matrix (before reescaling).
    Gets corners coordinates.
    Performs an Image rescaling to normalize pixel resolution to 10 meters per pixel.

    Params:
        band_filename (str) : Input band filename (jp2, tif).
        dst_crs (str) : Destination coordinate system (default is EPGS:4326).

    Returns:
        A tuple that contains:

            band (matrix) : matrix with image pixel information by position.
            top_left (tuple) : tuple of top left pixel latitude and longitude.
            bottom_right (tuple) : tuple of bottom right pixel latitude and longitude.

    """

    with rasterio.open(band_filename) as band_file:
        band = band_file.read(1).astype(np.float32)
        kwargs = band_file.meta
        init_crs = band_file.crs  # Get source coordinate system

        # Get latitude and longitude
        project = pyproj.Transformer.from_crs(
            init_crs, dst_crs, always_xy=True
        ).transform
        tl_lon, tl_lat = transform(project, Point(kwargs["transform"] * (0, 0))).bounds[
            0:2
        ]
        br_lon, br_lat = transform(
            project, Point(kwargs["transform"] * (band.shape[1] - 1, band.shape[0] - 1))
        ).bounds[0:2]

        top_left = (tl_lat, tl_lon)
        bottom_right = (br_lat, br_lon)

        # Reescale image
        img_resolution = kwargs["transform"][0]
        scale_factor = img_resolution / 10
        band = np.repeat(np.repeat(band, scale_factor, axis=0), scale_factor, axis=1)

        band[band == 0] = np.nan  # Change no data value to nan

    return band, kwargs


def get_latitude(rows, columns, initial_lat, final_lat):
    """
    Calculate latitude of a band pixel by pixel.

    Params:
        rows (int) : matrix number of rows.
        columns (int) : matrix number of columns.
        initial_lat (int) : value of the initial latitude (corresponding to the latitude of top left pixel of the band).
        final_lat (int) : value of the final latitude (corresponding to the latitude of bottom right pixel of the band).

    Returns:
        A matrix with latitude values for each pixel of a band.

    """

    pixel_lat = (
        final_lat - initial_lat
    ) / rows  # should be rows-1 since we want to calculate step size, and the number of steps is number of rows - 1. (we already start in first position)
    aux = np.arange(initial_lat, final_lat, pixel_lat)
    result = np.tile(aux, columns).transpose().flatten()

    return result


def get_longitude(columns, rows, initial_long, final_long):
    """
    Calculate longitude of a band pixel by pixel.

    Params:
        rows (int) : matrix number of rows.
        columns (int) : matrix number of columns.
        initial_long (int) : value of the initial longitude (corresponding to the longitude of top left pixel of the band).
        final_long (int) : value of the final longitude (corresponding to the longitude of bottom right pixel of the band).

    Returns:
        A matrix with longitude values for each pixel of a band.

    """

    pixel_long = (
        final_long - initial_long
    ) / columns  # should be columns-1 since we want to calculate step size, and the number of steps is number of columns - 1. (we already start in first position)
    aux = np.arange(initial_long, final_long, pixel_long)
    result = np.tile(aux, rows).flatten()

    return result


def classifier(
    band_filename, index_classifier: str, n_chunks: int = 10, output: str = None
):
    """
    Classifies the image specified in 'band_file' pixel by pixel using the input classifier.
    Due to the classification process consuming a large amount of resources, it can be adjusted to suit user available resources.
    If resources are not an issue, it can be left by default. Otherwise, increase the number of chunks used for the classification process.


    Params:
        band_filename (str) : Input band filename (jp2, tif).
        index_classifier (str) : Index to load the classifier from.
        n_chunks (int) : Number of chunks used for the classification process (default is 10).
        output (str) : Path to store the classified band to disk (tif).

    Returns:
        A matrix with the classification data, longitude and latitude for each pixel of the initial image.

    """
    band, kwargs = read_band(band_filename)
    rows, columns = band.shape

    # Preprocessing data

    band = band.flatten()
    aux_df = pd.DataFrame(band)
    indexes = aux_df[np.isnan(band)].index
    dt = pd.DataFrame(band[~np.isnan(band)].flatten())  # Removes nan

    # Classification
    prediction = np.array([])
    classifier = get_clasifier(index_classifier)
    array_split = np.array_split(dt, n_chunks)

    for array in array_split:
        prediction_chunk = classifier.predict(pd.DataFrame(array))
        prediction = np.append(prediction, prediction_chunk)
        del prediction_chunk

    # Reconstruction of the classification data, reshape final output

    array_pos = (
        indexes - np.indices(indexes.shape).flatten()
    )  # Shift position to original array
    dt = np.insert(prediction, array_pos, np.nan)
    classified_band = np.reshape(dt, (rows, columns))

    if output:
        print("writing to", output)
        # Update kwargs to reflect change in data type.
        kwargs.update(driver="GTiff", dtype=rasterio.float32, count=1)
        with rasterio.open(output, "w", **kwargs) as gtif:
            gtif.write(classified_band.astype(rasterio.float32), 1)

    # Return value for mongo
    return 0


def get_clasifier(index: str):
    client = Minio(
        f"{os.getenv('MINIO_HOST')}:{os.getenv('MINIO_PORT')}",
        access_key=os.getenv("MINIO_ACCESS_KEY"),
        secret_key=os.getenv("MINIO_SECRET_KEY"),
        secure=False,
    )
    filename = "classifier_" + index.upper() + ".joblib"
    path = os.getenv("TMP_DIR", "/tmp") + "/" + filename

    if not os.path.exists(path):
        client.fget_object(
            "etc-models",
            filename,
            path,
        )

    return joblib.load(path)
