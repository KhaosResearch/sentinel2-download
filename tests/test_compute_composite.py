import unittest
from unittest.mock import Mock, patch

from ds_download.compute_composite import seasonal_composite_exists


class SeasonalCompositeExistsTests(unittest.TestCase):
    def test_checks_seasonal_storage_prefix(self):
        composite_collection = Mock()
        composite_collection.find_one.return_value = {"title": "existing"}
        mongo_connection = Mock()
        mongo_connection.get_composite_collection_object.return_value = composite_collection

        with patch("ds_download.compute_composite.MongoConnection", return_value=mongo_connection):
            exists = seasonal_composite_exists("30STF", 2021, "Spring")

        self.assertTrue(exists)
        composite_collection.find_one.assert_called_once_with(
            {"S3BandsPrefix": {"$regex": "^30STF/2021/Spring/composites/"}}
        )


if __name__ == "__main__":
    unittest.main()
