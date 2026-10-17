"""Dental-Image-Organizer: local alignment and composition of clinical intraoral photos."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dental-image-organizer")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"
