import unittest
from unittest.mock import Mock, patch

from ds_download.request_limit import external_request_limit


class ExternalRequestLimitTests(unittest.TestCase):
    def test_noops_without_dask_client(self):
        with (
            patch("ds_download.request_limit.get_client", side_effect=ValueError),
            patch("ds_download.request_limit.Semaphore") as semaphore,
        ):
            with external_request_limit():
                pass

        semaphore.assert_not_called()

    def test_uses_cluster_semaphore_with_dask_client(self):
        semaphore = Mock()
        semaphore.return_value.__enter__ = Mock()
        semaphore.return_value.__exit__ = Mock(return_value=False)

        with (
            patch("ds_download.request_limit.get_client", return_value=Mock()),
            patch("ds_download.request_limit.Semaphore", semaphore),
        ):
            with external_request_limit():
                pass

        semaphore.assert_called_once_with(
            max_leases=8,
            name="sentinel-external-requests",
        )


if __name__ == "__main__":
    unittest.main()
