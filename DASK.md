# Dask Plan for 2017-2026 NDWI/NDVI Processing

## Goal

Store only monthly NDWI and NDVI indexes for 2017 through 2026.

Target MinIO layout:

```text
{tile}/{year}/{month}/composites/{composite_title}/indexes/ndwi.tif
{tile}/{year}/{month}/composites/{composite_title}/indexes/ndvi.tif
```

Where `composite_title` follows:

```text
S2S_MSIL2A_{first_sample_date}_NXXX_RXXX_T{tile}_{last_sample_date}_{hash}
```

## Why Dask

The work is naturally parallel by `tile + year + month`. Each task can process one month for one tile:

```text
process_tile_month(tile, year, month)
```

That task should:

1. Skip immediately if both monthly `ndwi.tif` and `ndvi.tif` already exist.
2. Download only the raw bands required for NDWI and NDVI.
3. Calculate product-level NDWI and NDVI.
4. Compute monthly pixel-wise means over those product-level indexes.
5. Upload monthly means to the composite-style MinIO path.
6. Delete product-level raw bands and product-level index files after both monthly indexes exist.

## Required Bands

NDWI needs:

```python
["B03_10m", "B08_10m"]
```

NDVI needs:

```python
["B04_10m", "B08_10m"]
```

Combined required bands:

```python
REQUIRED_BANDS = ["B03_10m", "B04_10m", "B08_10m"]
```

## Code Changes I Would Make

### 1. Generalize Index Configuration

In the Europe script, replace NDWI-only constants with:

```python
INDEXES = ["NDWI", "NDVI"]
REQUIRED_BANDS = ["B03_10m", "B04_10m", "B08_10m"]
```

### 2. Generalize Monthly Output Names

Replace NDWI-specific helpers with index-aware helpers:

```python
def monthly_index_prefix(tile, year, month):
    return f"{tile}/{year}/{month_name}/composites/"

def monthly_index_key(tile, year, month, product_titles, index_name):
    return f"{tile}/{year}/{month_name}/composites/{composite_title}/indexes/{index_name.lower()}.tif"
```

### 3. Generalize Existing Output Detection

Check for both:

```text
.../indexes/ndwi.tif
.../indexes/ndvi.tif
```

A `tile/year/month` task should skip only when both exist.

### 4. Generalize Monthly Mean Creation

Change `create_monthly_ndwi_mean(...)` to:

```python
create_monthly_index_mean(tile, year, month, index_name, mongo_col, minio_client, tmp_dir)
```

It should query:

```python
f"indexes.{index_name.lower()}.rasterS3Key"
```

Then compute:

```python
monthly_index = np.nanmean(np.stack(index_arrays), axis=0).astype(np.float32)
```

### 5. Add One Dask Task Function

Create a Dask entry script, for example:

```text
main_script_europe_dask.py
```

The task should be isolated:

```python
def process_tile_month(tile: str, year: int, month: int) -> str:
    load_dotenv(".env")
    os.environ["TMP_DIR"] = f"{base_tmp}/dask_{year}_{month:02}_{tile}_{os.getpid()}"

    # create Mongo and MinIO clients inside the task
    # skip if monthly NDWI and NDVI already exist
    # download required bands for this tile/month only
    # calculate NDWI and NDVI
    # create monthly means
    # cleanup product-level data only when both monthly outputs exist
```

Do not share Mongo, MinIO, rasterio datasets, or temp directories across tasks.

### 6. Submit Tasks

The Dask driver should submit:

```python
YEARS = range(2017, 2027)
MONTHS = range(1, 13)

futures = [
    client.submit(process_tile_month, tile, year, month)
    for year in YEARS
    for tile in tiles
    for month in MONTHS
]
```

Use `as_completed` so progress is visible:

```python
from dask.distributed import as_completed

for future in as_completed(futures):
    print(future.result())
```

## Running Locally

Start a local scheduler and workers:

```bash
uv run dask scheduler
```

In another terminal:

```bash
uv run dask worker tcp://127.0.0.1:8786 --nworkers 4 --nthreads 1
```

Then run the driver:

```bash
uv run python main_script_europe_dask.py --scheduler tcp://127.0.0.1:8786
```

## Running on Kubernetes

Use the existing Dask Helm setup as the base. Every worker needs:

- access to the same `.env` values;
- Google Cloud credentials mounted at `GOOGLE_APPLICATION_CREDENTIALS`;
- network access to MongoDB;
- network access to MinIO;
- the project package installed from the same code version;
- a worker-local writable `TMP_DIR`.

Driver command:

```bash
uv run python main_script_europe_dask.py --scheduler tcp://<dask-scheduler-host>:8786
```

## Recommended Concurrency

Start conservatively:

```text
10-20 workers
1 thread per worker
1 tile/month task per worker
```

This workload is likely network/storage bound, not CPU bound. Increase workers only if:

- MongoDB is stable;
- MinIO is stable;
- Google Cloud downloads are not throttled;
- worker disks are not filling with temp files.

## Safety Rules

- Never let two tasks process the same `tile/year/month`.
- Use unique `TMP_DIR` per task.
- Skip before downloading if both monthly outputs already exist.
- Cleanup product-level data only after both monthly `ndwi.tif` and `ndvi.tif` exist.
- Do not delete composite-style monthly outputs during cleanup.
- Keep Dask tasks idempotent so failed tasks can be retried.

## Suggested Command

For the full run:

```bash
uv run python main_script_europe_dask.py \
  --scheduler tcp://<scheduler-host>:8786 \
  --start-year 2017 \
  --end-year 2026
```

The script should treat `--end-year 2026` as inclusive.
