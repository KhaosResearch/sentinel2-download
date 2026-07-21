import logging
import os
import unittest
from unittest.mock import patch

from ds_download.observability import configure_logging


class ConfigureLoggingTests(unittest.TestCase):
    def setUp(self):
        configure_logging._configured = False
        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            if getattr(handler, "_ds_download_console", False) or getattr(handler, "_ds_download_otel", False):
                root_logger.removeHandler(handler)
                handler.close()

    def test_configure_logging_is_idempotent(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG", "OTEL_LOGS_EXPORTER": "none"}):
            configure_logging()
            configure_logging()

        root_logger = logging.getLogger()
        console_handlers = [
            handler
            for handler in root_logger.handlers
            if getattr(handler, "_ds_download_console", False)
        ]

        self.assertEqual(logging.DEBUG, root_logger.level)
        self.assertEqual(1, len(console_handlers))

    def test_non_otlp_exporter_keeps_local_logging_only(self):
        with patch.dict(os.environ, {"OTEL_LOGS_EXPORTER": "console"}):
            configure_logging()

        root_logger = logging.getLogger()
        otel_handlers = [
            handler
            for handler in root_logger.handlers
            if getattr(handler, "_ds_download_otel", False)
        ]

        self.assertEqual([], otel_handlers)

    def test_console_formatter_shows_extra_fields(self):
        with patch.dict(os.environ, {"OTEL_LOGS_EXPORTER": "none"}):
            configure_logging()

        root_logger = logging.getLogger()
        console_handler = next(
            handler
            for handler in root_logger.handlers
            if getattr(handler, "_ds_download_console", False)
        )
        record = logging.LogRecord(
            name="ds_download.download_using_sentinel_api",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="product download started",
            args=(),
            exc_info=None,
        )
        record.__dict__.update(
            {
                "s2.product": "S2A_MSIL2A_20210401T000000_N0000_R000_T30STG",
                "pipeline.season": "Spring",
            }
        )

        rendered = console_handler.format(record)

        self.assertIn('s2.product="S2A_MSIL2A_20210401T000000_N0000_R000_T30STG"', rendered)
        self.assertIn('pipeline.season="Spring"', rendered)


if __name__ == "__main__":
    unittest.main()
