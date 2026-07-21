import faulthandler
import json
import logging
import os
import sys


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_RESERVED_LOG_RECORD_ATTRS = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"asctime", "message"}


class _ExtraFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        fields = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOG_RECORD_ATTRS and not key.startswith("_")
        }
        if not fields:
            return message

        extras = " ".join(
            f"{key}={json.dumps(value, default=str)}"
            for key, value in sorted(fields.items())
        )
        return f"{message} {extras}"


def _log_level(level_name: str) -> int:
    return logging._nameToLevel.get(level_name.upper(), logging.INFO)


def _otel_logs_exporter_enabled() -> bool:
    exporter = os.environ.get("OTEL_LOGS_EXPORTER")
    if exporter:
        exporters = {item.strip().lower() for item in exporter.split(",")}
        return "none" not in exporters and "otlp" in exporters
    return bool(
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
    )


def _add_otel_logging_handler(root_logger: logging.Logger, level: int, service_name: str) -> None:
    if any(getattr(handler, "_ds_download_otel", False) for handler in root_logger.handlers):
        return

    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
    except ImportError:
        root_logger.warning("OpenTelemetry packages are not installed; OTLP log export disabled")
        return

    provider = LoggerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(provider)

    handler = LoggingHandler(level=level, logger_provider=provider)
    handler._ds_download_otel = True
    root_logger.addHandler(handler)


def configure_logging(service_name: str = "sentinel2-download") -> None:
    if configure_logging._configured:
        return

    faulthandler.enable(all_threads=True)

    level = _log_level(os.environ.get("LOG_LEVEL", "INFO"))
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not any(getattr(handler, "_ds_download_console", False) for handler in root_logger.handlers):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(_ExtraFormatter(_LOG_FORMAT))
        console_handler._ds_download_console = True
        root_logger.addHandler(console_handler)

    if _otel_logs_exporter_enabled():
        _add_otel_logging_handler(root_logger, level, service_name)

    configure_logging._configured = True


configure_logging._configured = False
