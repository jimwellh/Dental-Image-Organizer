"""Domain exceptions (spec §4.13). Each class carries a stable machine-readable `code`."""

from typing import Literal

SkipReason = Literal["video", "raw", "sidecar", "unknown"]


class DioError(Exception):
    code = "dio_error"


class UnsupportedFormatError(DioError):
    """The file is not a HEIC, JPEG, or PNG photo; `reason` groups skip summaries (spec §3.1)."""

    code = "unsupported_format"

    def __init__(self, message: str, *, reason: SkipReason) -> None:
        super().__init__(message)
        self.reason: SkipReason = reason


class CorruptImageError(DioError):
    code = "corrupt_image"


class InvalidNameError(DioError):
    code = "invalid_name"


class OutputTooLargeError(DioError):
    code = "output_too_large"


class StorageError(DioError):
    """Saved app state cannot be read back; the message says how to recover."""

    code = "storage_error"


class ModelUnavailableError(DioError):
    code = "model_unavailable"


class ModelIntegrityError(DioError):
    code = "model_integrity"
