# Project: sentinel2-download

## Purpose

Python package and scripts for downloading Sentinel-2 Level-2A products, storing
bands and derived rasters in MinIO, writing metadata to MongoDB, and creating
monthly composites locally or through Dask.

## Tech Stack

- Python 3.10+
- Packaging: `setuptools` in `setup.py`
- Data/geospatial: `rasterio`, `numpy`, `geojson`, `geomet`
- External services: Google Cloud Storage, MinIO, MongoDB, Copernicus Data Space
- Distributed execution: `dask.distributed`
- Environment loading: `python-dotenv`

## Commands

- Install runtime deps: `pip install -r requirements.txt`
- Install local package: `pip install -e .`
- Build package: `make build`
- Release package: `make release`
- Run local workflow: `python main_script.py`
- Run Dask workflow: `python main_script_dask.py`

No test, lint, or formatter command is currently configured in this repo. If you
add one, document it here and keep the change separate from behavior changes.

## Environment

Use `.env.template` as the source of required variables. Never commit real
credentials or generated `.env` files.

Required service/config variables include:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `GOOGLE_CLOUD_BUCKET_NAME`
- `MINIO_HOST`, `MINIO_PORT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`,
  `MINIO_BUCKET_NAME`
- `MONGO_HOST`, `MONGO_PORT`, `MONGO_USERNAME`, `MONGO_PASSWORD`,
  `MONGO_DATABASE_NAME`, `MONGO_COLLECTION_NAME`,
  `MONGO_COMPOSITE_COLLECTION_NAME`
- `TMP_DIR`
- `MAX_PRODUCTS_COMPOSITE`

Pipeline runs can download large imagery, write to `TMP_DIR`, upload to MinIO,
and mutate MongoDB collections. Do not run full workflows unless the user has
asked for execution and the target environment is clear.

## Project Map

- `main_script.py`: Sequential local workflow over years, months, and tiles.
- `main_script_dask.py`: Dask workflow that submits tile/month jobs to a
  scheduler. It currently creates a `Client` at module import time.
- `ds_download/download_using_sentinel_api.py`: Queries Copernicus product
  metadata and dispatches product downloads from Google Cloud.
- `ds_download/download_from_google_cloud.py`: Downloads product rasters from
  Google Cloud, uploads raw bands to MinIO, stores product metadata in MongoDB,
  and optionally calculates indexes.
- `ds_download/compute_composite.py`: Selects products from MongoDB, creates
  median composites, uploads composite rasters to MinIO, stores composite
  metadata, and optionally calculates indexes.
- `ds_download/raw_index_calculation.py`: Downloads bands from MinIO, computes
  spectral indexes, uploads index rasters, and updates metadata.
- `ds_download/band_arithmetic.py`: Raster math and spectral-index functions.
- `ds_download/mongo_connection.py`: MongoDB connection wrapper using env vars.
- `ds_download/minio_connection.py`: MinIO client wrapper using env vars.
- `cron_jobs_k8s/`: Kubernetes cron job packaging and update script.
- `clean_data/`: Sync/cleanup scripts for MongoDB and MinIO state.

## Coding Conventions

- Follow the existing module-level function style. Keep changes small and local.
- Prefer explicit datetime objects at public workflow boundaries.
- Preserve existing MinIO object layout:
  `{tile}/{year}/{Month}/products|composites/{title}/...`
- Keep product and composite metadata keys compatible with existing MongoDB
  documents unless a migration is explicitly requested.
- Use `pathlib.Path` for filesystem work when touching new code; existing code
  mixes `Path` and `os.path.join`, so avoid broad rewrites.
- Avoid adding dependencies unless the standard library or existing packages are
  insufficient.
- Avoid import-time network connections in new code. Existing
  `main_script_dask.py` connects at import time, so be careful when importing it
  from tests or tooling.

## Boundaries

- Never commit `.env`, cloud credentials, API keys, or generated service tokens.
- Do not run package release commands without explicit user approval.
- Do not delete MinIO objects or MongoDB documents unless that is the requested
  task and the target environment has been confirmed.
- Do not reformat the whole repository as part of a functional change.
- Before editing pipeline behavior, read the affected workflow script plus the
  relevant `ds_download/` module and one adjacent helper module.

## Context Packing For Future Tasks

When starting a task, load only the focused files:

- Dask orchestration: `main_script_dask.py`, `main_script.py`, and the called
  workflow functions in `ds_download/download_using_sentinel_api.py` and
  `ds_download/compute_composite.py`.
- Product download issues: `download_using_sentinel_api.py`,
  `download_from_google_cloud.py`, `mongo_connection.py`, and
  `minio_connection.py`.
- Composite issues: `compute_composite.py`, `raw_index_calculation.py`,
  `band_arithmetic.py`, `mongo_connection.py`, and `minio_connection.py`.
- Environment or deployment issues: `.env.template`, `README.md`,
  `dask_values_product_download.yaml`, and `cron_jobs_k8s/`.

If requirements conflict with existing behavior, surface the conflict and ask
before changing persistence format, object paths, or destructive cleanup logic.
