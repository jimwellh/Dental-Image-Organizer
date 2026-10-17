import io
from datetime import datetime, timedelta, timezone

import numpy as np
import pillow_heif
import pytest
from PIL import ExifTags, Image

from dental_image_organizer.core import imageio
from dental_image_organizer.errors import CorruptImageError, UnsupportedFormatError
from support.icc import display_p3_like_profile


def _gradient(width: int = 64, height: int = 48) -> np.ndarray:
    x = np.linspace(0, 255, width, dtype=np.float32)
    y = np.linspace(0, 255, height, dtype=np.float32)
    rgb = np.stack([*np.meshgrid(x, y), np.full((height, width), 128, np.float32)], axis=-1)
    return rgb.astype(np.uint8)


def _jpeg(img: Image.Image, exif: Image.Exif | None = None, **kwargs: object) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95, exif=(exif or Image.Exif()).tobytes(), **kwargs)
    return buf.getvalue()


def _exif(
    orientation: int = 1,
    taken: str | None = "2026:10:16 14:30:12",
    offset: str | None = "+08:00",
    gps: bool = False,
) -> Image.Exif:
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = orientation
    exif[ExifTags.Base.Make] = "Apple"
    exif[ExifTags.Base.Model] = "iPhone 16 Pro"
    exif[ExifTags.Base.Software] = "18.0"
    sub = exif.get_ifd(ExifTags.IFD.Exif)
    if taken:
        sub[ExifTags.Base.DateTimeOriginal] = taken
    if offset:
        sub[ExifTags.Base.OffsetTimeOriginal] = offset
    sub[ExifTags.Base.LensModel] = "iPhone 16 Pro back camera"
    if gps:
        exif.get_ifd(ExifTags.IFD.GPSInfo)[ExifTags.GPS.GPSLatitudeRef] = "N"
    return exif


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"\xff\xd8\xff\xe0" + bytes(20), "jpeg"),
        (b"\x89PNG\r\n\x1a\n" + bytes(20), "png"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic", "heic"),
        (b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1heic", "heic"),
    ],
)
def test_sniff_format_recognizes_supported_types(data: bytes, expected: str) -> None:
    assert imageio.sniff_format(data) == expected


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ", "video"),
        (b"II*\x00" + bytes(20), "raw"),
        (b'<?xml version="1.0" encoding="UTF-8"?><plist/>', "sidecar"),
        (b"GIF89a" + bytes(20), "unknown"),
    ],
)
def test_sniff_format_rejects_with_reason(data: bytes, reason: str) -> None:
    with pytest.raises(UnsupportedFormatError) as info:
        imageio.sniff_format(data)

    assert info.value.reason == reason


def test_extension_is_ignored_and_content_decides() -> None:
    png = io.BytesIO()
    Image.fromarray(_gradient()).save(png, format="PNG")

    assert imageio.decode(png.getvalue()).format == "png"


def test_decode_jpeg_reads_capture_time_with_offset() -> None:
    decoded = imageio.decode(_jpeg(Image.fromarray(_gradient()), _exif()))

    assert decoded.metadata.captured_at == datetime(
        2026, 10, 16, 14, 30, 12, tzinfo=timezone(timedelta(hours=8))
    )
    assert decoded.size == (64, 48)


def test_missing_capture_time_is_none() -> None:
    decoded = imageio.decode(_jpeg(Image.fromarray(_gradient()), _exif(taken=None)))

    assert decoded.metadata.captured_at is None


def test_capture_time_without_offset_is_naive() -> None:
    decoded = imageio.decode(_jpeg(Image.fromarray(_gradient()), _exif(offset=None)))

    assert decoded.metadata.captured_at == datetime(2026, 10, 16, 14, 30, 12)


def test_out_of_range_offset_keeps_the_naive_capture_time() -> None:
    decoded = imageio.decode(_jpeg(Image.fromarray(_gradient()), _exif(offset="+24:00")))

    assert decoded.metadata.captured_at == datetime(2026, 10, 16, 14, 30, 12)


@pytest.mark.parametrize("orientation", range(1, 9))
def test_every_exif_orientation_is_made_upright(orientation: int) -> None:
    upright = np.zeros((40, 60, 3), np.uint8)
    upright[:10, :10] = 255  # bright marker in the top-left corner
    stored = Image.fromarray(upright)
    # Store the pixels the way a camera would before tagging them with `orientation`.
    inverse = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_90,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_270,
    }
    if orientation in inverse:
        stored = stored.transpose(inverse[orientation])
    buf = io.BytesIO()
    stored.save(buf, format="PNG", exif=_exif(orientation=orientation).tobytes())

    rgb = imageio.decode(buf.getvalue()).rgb

    assert rgb.shape == (40, 60, 3)
    assert rgb[:10, :10].mean() > 200
    assert rgb[-10:, -10:].mean() < 50


def test_display_p3_profile_is_converted_to_srgb() -> None:
    p3_red = np.full((8, 8, 3), (200, 40, 40), np.uint8)
    buf = io.BytesIO()
    Image.fromarray(p3_red).save(buf, format="PNG", icc_profile=display_p3_like_profile())

    rgb = imageio.decode(buf.getvalue()).rgb

    # P3 red lies outside sRGB, so the converted red is more saturated than the stored value.
    assert rgb[0, 0, 0] > 200
    assert rgb[0, 0, 1] < 40


def test_untagged_and_srgb_tagged_pixels_are_unchanged() -> None:
    pixels = _gradient()
    tagged = _jpeg(Image.fromarray(pixels), icc_profile=imageio._SRGB_ICC)
    untagged = _jpeg(Image.fromarray(pixels))

    a = imageio.decode(tagged).rgb.astype(int)
    b = imageio.decode(untagged).rgb.astype(int)

    assert np.abs(a - b).max() <= 2


def test_broken_icc_profile_falls_back_to_srgb() -> None:
    buf = io.BytesIO()
    Image.fromarray(_gradient()).save(buf, format="PNG", icc_profile=b"not an icc profile")

    assert imageio.decode(buf.getvalue()).rgb.shape == (48, 64, 3)


@pytest.mark.parametrize("mode", ["L", "LA", "P", "RGBA", "I;16"])
def test_non_rgb_png_modes_decode_to_three_channel_uint8(mode: str) -> None:
    img = Image.fromarray(_gradient()).convert(mode)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    rgb = imageio.decode(buf.getvalue()).rgb

    assert rgb.dtype == np.uint8
    assert rgb.shape == (48, 64, 3)


def test_sixteen_bit_png_is_scaled_not_clipped() -> None:
    buf = io.BytesIO()
    Image.fromarray(np.full((8, 8), 40000, np.uint16)).save(buf, format="PNG")

    rgb = imageio.decode(buf.getvalue()).rgb

    assert np.abs(rgb.astype(int) - 156).max() <= 1  # 40000 / 257 = 155.6


def test_heic_decodes_with_metadata() -> None:
    heif = pillow_heif.from_pillow(Image.fromarray(_gradient()))
    buf = io.BytesIO()
    heif.save(buf, quality=90, exif=_exif().tobytes())

    decoded = imageio.decode(buf.getvalue())

    assert decoded.format == "heic"
    assert decoded.size == (64, 48)
    assert decoded.metadata.captured_at is not None


def test_truncated_file_is_corrupt() -> None:
    data = _jpeg(Image.fromarray(_gradient(640, 480)))

    with pytest.raises(CorruptImageError):
        imageio.decode(data[:200])


def test_oversized_image_is_rejected_before_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imageio, "MAX_PIXELS", 1000)

    with pytest.raises(CorruptImageError, match="limit"):
        imageio.decode(_jpeg(Image.fromarray(_gradient())))


def test_encode_keeps_capture_tags_resets_orientation_and_strips_gps() -> None:
    source = imageio.decode(_jpeg(Image.fromarray(_gradient()), _exif(orientation=1, gps=True)))

    out = Image.open(io.BytesIO(imageio.encode_jpeg(source.rgb, source.metadata)))
    exif = out.getexif()

    assert exif[ExifTags.Base.Orientation] == 1
    assert exif[ExifTags.Base.Make] == "Apple"
    assert ExifTags.Base.Software not in exif
    sub = exif.get_ifd(ExifTags.IFD.Exif)
    assert sub[ExifTags.Base.DateTimeOriginal] == "2026:10:16 14:30:12"
    assert sub[ExifTags.Base.OffsetTimeOriginal] == "+08:00"
    assert exif.get_ifd(ExifTags.IFD.GPSInfo) == {}
    assert out.info.get("icc_profile") == imageio._SRGB_ICC


def test_encode_can_keep_gps() -> None:
    source = imageio.decode(_jpeg(Image.fromarray(_gradient()), _exif(gps=True)))

    out = Image.open(io.BytesIO(imageio.encode_jpeg(source.rgb, source.metadata, strip_gps=False)))

    assert out.getexif().get_ifd(ExifTags.IFD.GPSInfo)[ExifTags.GPS.GPSLatitudeRef] == "N"


def test_resize_long_edge_downscales_and_reports_scale() -> None:
    rgb = np.zeros((1000, 3000, 3), np.uint8)

    small, scale = imageio.resize_long_edge(rgb, 1500)

    assert small.shape == (500, 1500, 3)
    assert scale == pytest.approx(0.5)


def test_resize_long_edge_does_not_upscale_by_default() -> None:
    rgb = np.zeros((100, 200, 3), np.uint8)

    same, scale = imageio.resize_long_edge(rgb, 1600)

    assert same is rgb
    assert scale == 1.0
