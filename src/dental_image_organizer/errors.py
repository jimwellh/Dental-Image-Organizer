"""Domain exceptions (spec §4.13). Each class carries a stable machine-readable `code`."""

from collections.abc import Mapping
from typing import Any, Literal

SkipReason = Literal["video", "raw", "sidecar", "unknown"]


class DioError(Exception):
    code = "dio_error"

    def __init__(self, message: str = "", *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, Any] = dict(details) if details else {}


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


class NotFoundError(DioError):
    code = "not_found"


class PatientNotFoundError(NotFoundError):
    code = "patient_not_found"


class CaseNotFoundError(NotFoundError):
    code = "case_not_found"


class PhotoNotFoundError(NotFoundError):
    code = "photo_not_found"


class JobNotFoundError(NotFoundError):
    code = "job_not_found"


class TeachSessionNotFoundError(NotFoundError):
    code = "teach_session_not_found"


class ConflictError(DioError):
    code = "conflict"


class JobRunningError(ConflictError):
    code = "job_running"


class CaseExistsError(ConflictError):
    code = "case_exists"


class NotReadyError(ConflictError):
    code = "not_ready"


class UpdateUnavailableError(ConflictError):
    """In-app update cannot run here; the message names the Terminal command instead."""

    code = "update_unavailable"


class LauncherError(DioError):
    code = "launcher_error"


class InvalidRequestError(DioError):
    code = "invalid_request"


class InvalidEditError(InvalidRequestError):
    code = "invalid_edit"


class InvalidArchiveError(DioError):
    code = "invalid_archive"


class PathOutsideLibraryError(DioError):
    code = "path_outside_library"
