# ds\_download

`ds_download` is a Python package designed to facilitate the downloading and processing of Sentinel-2 satellite imagery, including creating composites and calculating various spectral indices. This package automates interactions with the Sentinel API and handles composite creation through cloud-based storage like MinIO and Google Cloud Storage.

## Features

- **Download Sentinel-2 Data**: Fetches Sentinel-2 products using Copernicus Open Access Hub API and Google Cloud.
- **Composite Creation**: Automatically creates pixel-wise median composites for specified date ranges.
- **Spectral Indices Calculation**: Supports the computation of various indices like NDVI, NDWI, NDSI, EVI, and more.
- **MongoDB and MinIO Integration**: Stores metadata in MongoDB and handles data through MinIO for distributed processing.

## Requirements

- Python 3.10+
- A working MongoDB and MinIO instance
- Access to Google Cloud

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/KhaosResearch/sentinel2-download.git
   cd ds_download
   ```

2. Install dependencies and the local package with `uv`:

   ```bash
   uv sync
   ```

3. Set up your environment variables in a `.env` file:

4. For pip-based environments, install the local package directly:

   ```bash
   pip install .
   ```

## Usage

For usage examples, refer to `main_script.py` and `main_script_dask.py` in the repository. These scripts demonstrate how to use the library both locally and in a Dask cluster environment.

### Download Sentinel-2 Products

```python
from ds_download.download_using_sentinel_api import download_product_using_sentinel_api
from datetime import datetime

# Download products for a specific tile and date range
start_date = datetime(2021, 4, 1)
end_date = datetime(2021, 4, 30)
tile_id = "29SLC"

download_product_using_sentinel_api(
    calculate_raw_indexes=False,
    calculate_intermediate_products=True,
    from_date=start_date,
    to_date=end_date,
    tile_id=tile_id
)
```

### Create a Composite

```python
from ds_download.compute_composite import create_composite_by_tile_and_date
from datetime import datetime

# Create a composite for a specific tile and date range
tile_id = "29SLC"
start_date = datetime(2021, 4, 1)
end_date = datetime(2021, 4, 30)

create_composite_by_tile_and_date(
    calculate_raw_indexes=True,
    calculate_intermediate_products=False,
    tile=tile_id,
    start_date=start_date,
    end_date=end_date,
    min_useful_data_percentage=30
)
```

### Create Seasonal Mean Rasters

Seasonal mode is opt-in. It creates four mean composites per tile/year:
Winter, Spring, Summer, and Autumn. Product indexes are calculated first, then
both bands and product index rasters are averaged pixel-wise into seasonal
rasters. The same `MINIO_BUCKET_NAME` variable is used; point it at the bucket
intended for seasonal outputs before running this mode.

```bash
python main_script.py --composite-period seasonal
```

Add `--quantize-rasters` to write generated bands and indexes as compressed
integer GeoTIFFs. Bands are rounded to `uint16`; indexes are scaled by `0.0001`
and stored as `int16`.

```bash
python main_script.py --composite-period seasonal --quantize-rasters
```

By default, seasonal mode deletes the source product rasters from MinIO after
the seasonal outputs are uploaded successfully. Keep them for debugging with:

```bash
python main_script.py --composite-period seasonal --keep-products
```

The Dask script accepts the same period flag:

```bash
python main_script_dask.py --scheduler "<dask-scheduler-host>:<dask-scheduler-port>" --composite-period seasonal
```

### OpenTelemetry Logs

Pipeline scripts emit standard Python log records and can export them through
OpenTelemetry OTLP when a collector endpoint is configured:

```bash
LOG_LEVEL=DEBUG
OTEL_LOGS_EXPORTER=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
```

Leave `OTEL_LOGS_EXPORTER=none` for local console-only logs.

You can also run the one-tile seasonal pipeline directly from
`download_product_using_sentinel_api`. This reads `app_data/seasons.json`,
creates pixel-wise median seasonal composites for bands and product indexes,
and deletes non-composite MinIO objects for that tile when cleanup is enabled:

```python
download_product_using_sentinel_api(
    False,
    False,
    tile_id="31STF",
    run_seasonal_pipeline=True,
)
```

### Dask Integration for Distributed Processing

```python
from dask.distributed import Client
from ds_download.download_using_sentinel_api import download_product_using_sentinel_api
from ds_download.compute_composite import create_composite_by_tile_and_date

client = Client("<dask-scheduler-host>:<dask-scheduler-port>")

def process_month(year, month, tile):
    # Define logic for processing
    ...

futures = [client.submit(process_month, year, month, tile) for year in years for tile in tiles]
results = client.gather(futures)

for result in results:
    print(result)
```

## Deploying a Dask Cluster in Kubernetes

To deploy a Dask cluster compatible with the `ds_download` library in Kubernetes, you can use Helm with custom values. This section will guide you through deploying the Dask cluster and the necessary secrets to integrate with MinIO, MongoDB, and Google Cloud.

### Prerequisites

- Kubernetes cluster up and running.
- Helm installed.
- Access to your Kubernetes cluster context.

### Step 1: Deploy Dask using Helm

Refer to the `dask_values_product_download.yaml` file provided in the repository to configure the Dask cluster for use with `ds_download`. Ensure to fill in all placeholder values (e.g., MinIO credentials, MongoDB credentials) before deploying. Additionally, since the Kubernetes nodes need access to the `ds_download` library, you will need to build the package and upload it to your own package repository.

Deploy the Dask cluster using Helm:

```bash
helm repo add dask https://helm.dask.org/
helm install dask-cluster dask/dask -f dask_values_product_download.yaml
```

### Step 2: Create Kubernetes Secrets

To allow Dask workers to authenticate with Google Cloud, MongoDB, and MinIO, you need to create the appropriate Kubernetes secrets. Here's an example of how to create a secret for Google Cloud credentials:

```bash
kubectl create secret generic gcloud-credentials --from-file=path_to_your_gcloud_credentials.json
```

Ensure that the secret name matches the one specified in your `values.yaml` file.

### Step 3: Verify Deployment

After deploying the Dask cluster and creating the necessary secrets, verify that the scheduler and workers are running:

```bash
kubectl get pods
```

You should see pods for the Dask scheduler and multiple workers, indicating that the cluster is successfully deployed.

Now, your Dask cluster is ready to work with the `ds_download` library for distributed processing of Sentinel-2 data.
