# Dask Pipeline Notes

## Repositories

- This repo: [KhaosResearch/sentinel2-download](https://github.com/KhaosResearch/sentinel2-download)
- Parent repo: [KhaosResearch/landcoverpy](https://github.com/KhaosResearch/landcoverpy)

`sentinel2-download` is used by `landcoverpy` as the Sentinel-2 download and processing component. This branch documents and changes `sentinel2-download`; `landcoverpy` has not yet been modified to consume these branch changes.

## Branch Changes

### Functionality

- Added seasonal composite support alongside monthly composites.
- Added `app_data/seasons.json` with 2021 season windows:
  - `spring`: 2021-03-01 to 2021-04-15
  - `flowering`: 2021-05-01 to 2021-05-31
  - `summer`: 2021-06-01 to 2021-07-31
  - `autumn`: 2021-10-01 to 2021-11-30
- Added Dask seasonal processing in `main_script_dask.py`.
- Seasonal Dask mode processes one season at a time, submitting all tiles for that season before moving to the next.
- Added skipping for already-uploaded seasonal composites via `seasonal_composite_exists`.
- Added seasonal composite storage under `{tile}/{year}/{season}/composites/...`.
- Added seasonal mean composites that include raw bands and product index rasters.
- Added optional product cleanup after seasonal composites are created.
- Added optional raster quantization with `--quantize-rasters`.
- Added Sentinel API retry/rate-limit handling for transient API failures.
- Added structured logging for pipeline year, season, month, tile, task status, and errors.
- Added tests covering seasonal composite helpers, Dask seasonal task behavior, and logging.

## Run From Scratch

### 1. Install `uv`

If `uv` is not installed, use the official installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart the shell, or load the updated path:

```bash
source "$HOME/.local/bin/env"
```

Check the installation:

```bash
uv --version
```

### 2. Install Project Dependencies

From the repository root:

```bash
uv sync
```

This creates the local virtual environment in `.venv/` and installs the package dependencies from `pyproject.toml` and `uv.lock`.

### 3. Configure Environment Variables

Create a local `.env` from the template and fill in the real service values:

```bash
cp .env.template .env
```

Required services include Google Cloud credentials, MinIO, MongoDB, `TMP_DIR`, and `MAX_PRODUCTS_COMPOSITE`. Do not commit `.env`.

### 4. Start the Dask Scheduler

On the main machine:

```bash
setsid -f .venv/bin/dask scheduler \
  --host 0.0.0.0 \
  --port 8786 \
  --dashboard-address :8787 \
  > dask-scheduler.log 2>&1 < /dev/null
```

The scheduler address used by workers and the driver will be:

```bash
tcp://<scheduler-machine-ip>:8786
```

Use `tcp://127.0.0.1:8786` only when the scheduler, workers, and driver all run on the same machine.

### 5. Start Dask Workers

Workers can run on the same machine as the scheduler or on other machines. Remote worker machines must have:

- network access to the scheduler address
- this repository checked out
- dependencies installed with `uv sync`
- the same required `.env` service configuration
- access to MinIO, MongoDB, Google Cloud credentials, and a writable temporary directory

Start one worker on any machine:

```bash
setsid -f env TMP_DIR=/tmp/sentinel2-dask/worker-1 MALLOC_TRIM_THRESHOLD_=0 GDAL_CACHEMAX=512 \
  .venv/bin/dask worker tcp://<scheduler-machine-ip>:8786 \
  --nthreads 1 \
  --memory-limit 24GB \
  --local-directory /tmp/sentinel2-dask/worker-1 \
  > dask-worker-1.log 2>&1 < /dev/null
```

Start `x` workers at a time on the current machine:

```bash
WORKERS=4
SCHEDULER=tcp://<scheduler-machine-ip>:8786

for i in $(seq 1 "$WORKERS"); do
  setsid -f env TMP_DIR="/tmp/sentinel2-dask/worker-$i" MALLOC_TRIM_THRESHOLD_=0 GDAL_CACHEMAX=512 \
    .venv/bin/dask worker "$SCHEDULER" \
    --nthreads 1 \
    --memory-limit 24GB \
    --local-directory "/tmp/sentinel2-dask/worker-$i" \
    > "dask-worker-$i.log" 2>&1 < /dev/null
done
```

### 6. Run the Driver

Run the seasonal Dask download/composite pipeline:

```bash
setsid -f env PYTHONUNBUFFERED=1 .venv/bin/python main_script_dask.py \
  --scheduler tcp://<scheduler-machine-ip>:8786 \
  --composite-period seasonal \
  --quantize-rasters \
  > dask-driver.log 2>&1 < /dev/null
```

Run the monthly Dask pipeline:

```bash
setsid -f env PYTHONUNBUFFERED=1 .venv/bin/python main_script_dask.py \
  --scheduler tcp://<scheduler-machine-ip>:8786 \
  --composite-period monthly \
  --quantize-rasters \
  > dask-driver.log 2>&1 < /dev/null
```

### 7. Retry Failed or Unfinished Seasonal Work

Stop only the driver:

```bash
pkill -f "main_script_dask.py"
```

Then submit it again:

```bash
setsid -f env PYTHONUNBUFFERED=1 .venv/bin/python main_script_dask.py \
  --scheduler tcp://<scheduler-machine-ip>:8786 \
  --composite-period seasonal \
  --quantize-rasters \
  > dask-driver-retry.log 2>&1 < /dev/null
```

Completed seasonal composites are skipped on rerun; failed or unfinished tiles are retried.

## Quick Command Reference

Start the scheduler:

```bash
setsid -f .venv/bin/dask scheduler \
  > dask-scheduler.log 2>&1 < /dev/null
```

Start one worker:

```bash
setsid -f env TMP_DIR=/tmp/sentinel2-dask/worker-1 MALLOC_TRIM_THRESHOLD_=0 GDAL_CACHEMAX=512 \
  .venv/bin/dask worker tcp://127.0.0.1:8786 \
  --nthreads 1 \
  --memory-limit 24GB \
  --local-directory /tmp/sentinel2-dask/worker-1 \
  > dask-worker-1.log 2>&1 < /dev/null
```

Start `x` workers at a time:

```bash
WORKERS=4
for i in $(seq 1 "$WORKERS"); do
  setsid -f env TMP_DIR="/tmp/sentinel2-dask/worker-$i" MALLOC_TRIM_THRESHOLD_=0 GDAL_CACHEMAX=512 \
    .venv/bin/dask worker tcp://127.0.0.1:8786 \
    --nthreads 1 \
    --memory-limit 24GB \
    --local-directory "/tmp/sentinel2-dask/worker-$i" \
    > "dask-worker-$i.log" 2>&1 < /dev/null
done
```

Run the seasonal Dask download/composite pipeline:

```bash
setsid -f env PYTHONUNBUFFERED=1 .venv/bin/python main_script_dask.py \
  --scheduler tcp://127.0.0.1:8786 \
  --composite-period seasonal \
  --quantize-rasters \
  > dask-driver.log 2>&1 < /dev/null
```

Run the monthly Dask pipeline:

```bash
setsid -f env PYTHONUNBUFFERED=1 .venv/bin/python main_script_dask.py \
  --scheduler tcp://127.0.0.1:8786 \
  --composite-period monthly \
  --quantize-rasters \
  > dask-driver.log 2>&1 < /dev/null
```

Retry failed or unfinished seasonal work:

```bash
pkill -f "main_script_dask.py"

setsid -f env PYTHONUNBUFFERED=1 .venv/bin/python main_script_dask.py \
  --scheduler tcp://127.0.0.1:8786 \
  --composite-period seasonal \
  --quantize-rasters \
  > dask-driver-retry.log 2>&1 < /dev/null
```

Completed seasonal composites are skipped on rerun; failed or unfinished tiles are retried.
