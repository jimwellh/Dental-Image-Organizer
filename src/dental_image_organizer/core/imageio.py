"""Decoding, metadata, resizing, and JPEG encoding (spec §4.4)."""

from __future__ import annotations

import io
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast

import cv2
import numpy as np
import numpy.typing as npt
import pi_heif
import structlog
from PIL import ExifTags, Image, ImageCms, ImageOps

from dental_image_organizer.errors import CorruptImageError, UnsupportedFormatError

log = structlog.get_logger(__name__)

pi_heif.register_heif_opener()

MAX_PIXELS = 200_000_000
# libjpeg cannot encode an image with a side longer than this; Pillow then fails with
# "broken data stream", so callers that build wide canvases must stay within it.
MAX_JPEG_DIMENSION = 65500
# Pillow raises DecompressionBombError above twice this value, so its guard matches ours.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS // 2

RGBImage = npt.NDArray[np.uint8]
ImageFormat = Literal["heic", "jpeg", "png"]

_HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"})
_VIDEO_BRANDS = frozenset({b"qt  ", b"isom", b"iso2", b"mp41", b"mp42", b"M4V ", b"avc1"})

# Tags kept on export; maker notes, thumbnails, and everything else are dropped.
_KEPT_BASE_TAGS = (ExifTags.Base.Make, ExifTags.Base.Model)
_KEPT_EXIF_TAGS = (
    ExifTags.Base.DateTimeOriginal,
    ExifTags.Base.OffsetTimeOriginal,
    ExifTags.Base.LensMake,
    ExifTags.Base.LensModel,
)
_OFFSET = re.compile(r"([+-])(\d{2}):(\d{2})")

_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
_SRGB_ICC = _SRGB.tobytes()


@dataclass(frozen=True)
class Metadata:
    captured_at: datetime | None = None
    base_tags: Mapping[int, Any] = field(default_factory=dict)
    exif_tags: Mapping[int, Any] = field(default_factory=dict)
    gps_tags: Mapping[int, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecodedImage:
    rgb: RGBImage
    metadata: Metadata
    format: ImageFormat

    @property
    def size(self) -> tuple[int, int]:
        return int(self.rgb.shape[1]), int(self.rgb.shape[0])


def sniff_format(data: bytes) -> ImageFormat:
    """Identify the format from magic bytes; file extensions are not trusted."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[4:8] == b"ftyp":
        brands = _ftyp_brands(data)
        if brands & _HEIF_BRANDS:
            return "heic"
        if brands & _VIDEO_BRANDS:
            raise UnsupportedFormatError("Videos are not supported.", reason="video")
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        raise UnsupportedFormatError(
            "DNG/ProRAW is not supported; export as HEIC or JPEG.", reason="raw"
        )
    if data[:64].lstrip().startswith(b"<?xml"):
        # Photos.app writes .AAE edit sidecars as XML property lists next to edited photos.
        raise UnsupportedFormatError(
            "Photos edit sidecars (.AAE) are not images.", reason="sidecar"
        )
    raise UnsupportedFormatError(
        "Unrecognized file type; supported formats are HEIC, JPEG, and PNG.", reason="unknown"
    )


def decode(data: bytes) -> DecodedImage:
    """Decode to upright sRGB pixels plus the metadata kept for export."""
    fmt = sniff_format(data)
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise CorruptImageError(
                    f"Image is {width}x{height}; the limit is {MAX_PIXELS // 1_000_000} MP."
                )
            metadata = _read_metadata(img.getexif())
            icc = img.info.get("icc_profile")
            upright = ImageOps.exif_transpose(img)
            rgb = np.array(_to_srgb(upright, icc), dtype=np.uint8)
    except Image.DecompressionBombError as exc:
        raise CorruptImageError(f"Image exceeds the {MAX_PIXELS // 1_000_000} MP limit.") from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise CorruptImageError(f"Cannot decode {fmt.upper()} image: {exc}") from exc
    return DecodedImage(rgb=rgb, metadata=metadata, format=fmt)


def read_capture_time(data: bytes) -> datetime | None:
    """The EXIF capture time from the header alone, or None when no header can be parsed.

    A copy cut short keeps its header, so a photo whose pixels fail to decode can still be
    ranked in shooting order.
    """
    try:
        # Opening only parses the header; no pixel is decoded.
        with Image.open(io.BytesIO(data)) as img:
            return _read_metadata(img.getexif()).captured_at
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        return None


def resize_long_edge(
    rgb: RGBImage, long_edge: int, *, upscale: bool = False
) -> tuple[RGBImage, float]:
    """Resize so the longer side is `long_edge`; returns the image and the applied scale."""
    h, w = rgb.shape[:2]
    scale = long_edge / max(h, w)
    if scale >= 1.0 and not upscale:
        return rgb, 1.0
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    resized = cast(RGBImage, cv2.resize(rgb, size, interpolation=interpolation))
    return resized, size[0] / w


def encode_jpeg(
    rgb: RGBImage,
    metadata: Metadata | None = None,
    *,
    quality: int = 92,
    strip_gps: bool = True,
) -> bytes:
    exif = Image.Exif()
    # Pixels are already upright; a stale orientation tag would rotate them a second time.
    exif[ExifTags.Base.Orientation] = 1
    if metadata is not None:
        for tag, value in metadata.base_tags.items():
            exif[tag] = value
        if metadata.exif_tags:
            exif.get_ifd(ExifTags.IFD.Exif).update(metadata.exif_tags)
        if not strip_gps and metadata.gps_tags:
            exif.get_ifd(ExifTags.IFD.GPSInfo).update(metadata.gps_tags)
    buffer = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb)).save(
        buffer,
        format="JPEG",
        quality=quality,
        subsampling=0,
        exif=exif.tobytes(),
        icc_profile=_SRGB_ICC,
    )
    return buffer.getvalue()


def _ftyp_brands(data: bytes) -> frozenset[bytes]:
    box_size = int.from_bytes(data[0:4], "big")
    end = min(len(data), max(box_size, 16))
    brands = {data[8:12]}
    brands.update(data[i : i + 4] for i in range(16, end - 3, 4))
    return frozenset(brands)


def _read_metadata(exif: Image.Exif) -> Metadata:
    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    return Metadata(
        captured_at=_parse_capture_time(
            exif_ifd.get(ExifTags.Base.DateTimeOriginal),
            exif_ifd.get(ExifTags.Base.OffsetTimeOriginal),
        ),
        base_tags={tag: exif[tag] for tag in _KEPT_BASE_TAGS if tag in exif},
        exif_tags={tag: exif_ifd[tag] for tag in _KEPT_EXIF_TAGS if tag in exif_ifd},
        gps_tags=dict(exif.get_ifd(ExifTags.IFD.GPSInfo)),
    )


def _parse_capture_time(value: object, offset: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        captured = datetime.strptime(value.strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    match = _OFFSET.fullmatch(offset.strip("\x00 ")) if isinstance(offset, str) else None
    if match is None:
        return captured
    sign = 1 if match.group(1) == "+" else -1
    delta = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
    try:
        return captured.replace(tzinfo=timezone(sign * delta))
    except ValueError:
        # An offset of 24 h or more is a broken tag; the wall-clock time is still usable.
        return captured


def _to_8bit(img: Image.Image) -> Image.Image:
    # Pillow's convert() clips 16-bit samples at 255 instead of scaling them.
    if img.mode not in ("I", "I;16", "I;16B", "I;16L"):
        return img
    samples = np.asarray(img, dtype=np.float64) / 257.0
    return Image.fromarray(np.clip(np.rint(samples), 0, 255).astype(np.uint8))


def _to_srgb(img: Image.Image, icc: bytes | None) -> Image.Image:
    rgb = img if img.mode == "RGB" else _to_8bit(img).convert("RGB")
    if not icc:
        return rgb
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        converted = ImageCms.profileToProfile(
            rgb, source, _SRGB, renderingIntent=ImageCms.Intent.PERCEPTUAL, outputMode="RGB"
        )
    except (ImageCms.PyCMSError, OSError) as exc:
        # A broken or non-RGB profile must not block import; treating pixels as sRGB is the
        # least surprising fallback.
        log.warning("icc_profile_ignored", error=str(exc))
        return rgb
    return converted if converted is not None else rgb
