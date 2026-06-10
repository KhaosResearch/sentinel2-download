# Project: sentinel2-download

## Purpose
- Python package `ds_download` for downloading Sentinel-2 products, calculating raster indexes/intermediate products, and creating composites.
- Integrates with Copernicus Data Space, Google Cloud Storage, MinIO, MongoDB, rasterio/GDAL-compatible GeoTIFF/JP2 processing, Dask, and Kubernetes cron jobs.

## Tech Stack
- Python 3.10+
- Package metadata in `setup.py`; pinned runtime dependencies in `requirements.txt`.
- Main geospatial/data dependencies include `rasterio`, `numpy`, `google-cloud-storage`, `minio`, `pymongo`, `requests`, `geojson`, and `geomet`.

## Commands
- Install local package: `pip install .`
- Install dependencies: `pip install -r requirements.txt`
- Build package: `make build` or `python -m build`
- Release package: `make release` uploads `dist/ds_download*` to the configured `khaos` repository.
- There is no committed pytest configuration or test suite yet. Do not claim tests exist unless adding them.

## Environment
- Use `.env.template` as the source of required environment variables.
- Runtime code expects credentials/config for Google Cloud, MinIO, MongoDB, `TMP_DIR`, and `MAX_PRODUCTS_COMPOSITE`.
- Never commit `.env`, real credentials, service-account JSON, access keys, or generated secrets.

## Safety Boundaries
- Do not run scripts that hit Copernicus, Google Cloud, MinIO, MongoDB, Dask, or Kubernetes unless the user explicitly asks for an integration run.
- Treat `main_script.py`, `main_script_dask.py`, and `cron_jobs_k8s/update_andalusia.py` as operational examples that can process many products and write remote state.
- Be careful with cleanup code around `TMP_DIR`; verify paths before adding destructive deletion logic.
- Ask before changing release configuration, package names, pinned dependency versions, Kubernetes manifests, or storage/database schemas.

## Code Conventions
- Prefer existing module-level function style and explicit imports from `ds_download`.
- Keep public functions typed where practical; this codebase uses docstrings heavily, so update docstrings when changing behavior.
- Preserve raster metadata (`kwargs`, transforms, CRS, nodata, dtype, driver) when modifying raster processing functions.
- Use `pathlib.Path` for new filesystem-heavy code unless local surrounding code is already using string paths.
- Avoid broad refactors while fixing focused behavior; this repo contains operational scripts and remote integrations.

## Context Loading Before Changes
- Before editing download flows, read `ds_download/download_using_sentinel_api.py` and `ds_download/download_from_google_cloud.py`.
- Before editing composite logic, read `ds_download/compute_composite.py`, `ds_download/raw_index_calculation.py`, and `ds_download/band_arithmetic.py`.
- Before editing storage/database access, read `ds_download/minio_connection.py` and `ds_download/mongo_connection.py`.
- Before editing deployment automation, read the files under `cron_jobs_k8s/` and `dask_values_product_download.yaml`.

## Current Notes
- `test_comprossed_tiffs.py` is currently untracked and appears to be a standalone TIFF conversion/check script, not an established project test suite.
- The repository currently has no persistent lint, type-check, or unit-test command configured.
