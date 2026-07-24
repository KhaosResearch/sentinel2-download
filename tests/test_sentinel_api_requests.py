import unittest
from unittest.mock import Mock, patch

from ds_download.download_using_sentinel_api import find_products_sentinel_api_by_tile_id


class SentinelApiRequestTests(unittest.TestCase):
    def test_retries_throttled_catalogue_response(self):
        throttled = Mock(
            status_code=429,
            headers={},
            text='{"error":"too many requests"}',
        )
        throttled.json.return_value = {"error": "too many requests"}
        success = Mock(status_code=200, headers={}, text='{"value":[]}')
        success.json.return_value = {"value": []}

        with (
            patch("ds_download.download_using_sentinel_api.requests.get", side_effect=[throttled, success]) as get,
            patch("ds_download.download_using_sentinel_api.time.sleep") as sleep,
        ):
            products = find_products_sentinel_api_by_tile_id("29SPS", "2021-03-01", "2021-04-16")

        self.assertEqual([], products)
        self.assertEqual(2, get.call_count)
        sleep.assert_called_once()

    def test_missing_value_payload_raises_useful_error(self):
        response = Mock(
            status_code=200,
            headers={},
            text='{"error":"bad response"}',
        )
        response.json.return_value = {"error": "bad response"}

        with patch("ds_download.download_using_sentinel_api.requests.get", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "missing 'value'.*bad response"):
                find_products_sentinel_api_by_tile_id("29SPS", "2021-03-01", "2021-04-16")


if __name__ == "__main__":
    unittest.main()
