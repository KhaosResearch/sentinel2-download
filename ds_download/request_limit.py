from contextlib import contextmanager, nullcontext

from dask.distributed import Semaphore, get_client


MAX_CONCURRENT_EXTERNAL_REQUESTS = 3
REQUEST_SEMAPHORE_NAME = "sentinel-external-requests"


@contextmanager
def external_request_limit():
    try:
        get_client()
    except ValueError:
        with nullcontext():
            yield
        return

    with Semaphore(
        max_leases=MAX_CONCURRENT_EXTERNAL_REQUESTS,
        name=REQUEST_SEMAPHORE_NAME,
    ):
        yield
