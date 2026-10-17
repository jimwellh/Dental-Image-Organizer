"""Build a minimal ICC v2 matrix/TRC display profile for tests.

Pillow can only create sRGB profiles, but the decoder must be tested against a wide-gamut
profile like the Display P3 profile iPhones embed.
"""

import struct

# Display P3 colorants adapted to the D50 profile connection space.
DISPLAY_P3_D50 = {
    b"rXYZ": (0.5151, 0.2412, -0.0011),
    b"gXYZ": (0.2920, 0.6922, 0.0419),
    b"bXYZ": (0.1571, 0.0666, 0.7841),
}
_D50 = (0.9642, 1.0, 0.8249)


def _s15f16(value: float) -> bytes:
    return struct.pack(">i", round(value * 65536))


def _xyz(x: float, y: float, z: float) -> bytes:
    return b"XYZ " + bytes(4) + _s15f16(x) + _s15f16(y) + _s15f16(z)


def _curve(gamma: float) -> bytes:
    return b"curv" + bytes(4) + struct.pack(">IH", 1, round(gamma * 256)) + bytes(2)


def _desc(text: str) -> bytes:
    ascii_text = text.encode("ascii") + b"\x00"
    body = b"desc" + bytes(4) + struct.pack(">I", len(ascii_text)) + ascii_text
    body += struct.pack(">II", 0, 0) + struct.pack(">HB", 0, 0) + bytes(67)
    return body


def _text(text: str) -> bytes:
    return b"text" + bytes(4) + text.encode("ascii") + b"\x00"


def _pad(data: bytes) -> bytes:
    return data + bytes(-len(data) % 4)


def matrix_profile(colorants: dict[bytes, tuple[float, float, float]], gamma: float = 2.2) -> bytes:
    tags = [
        (b"desc", _desc("Test RGB")),
        (b"cprt", _text("Public domain")),
        (b"wtpt", _xyz(*_D50)),
        *((sig, _xyz(*xyz)) for sig, xyz in colorants.items()),
        *((sig, _curve(gamma)) for sig in (b"rTRC", b"gTRC", b"bTRC")),
    ]
    table_size = 4 + 12 * len(tags)
    offset = 128 + table_size
    table, data = struct.pack(">I", len(tags)), b""
    for sig, payload in tags:
        table += sig + struct.pack(">II", offset + len(data), len(payload))
        data += _pad(payload)
    size = 128 + table_size + len(data)
    header = (
        struct.pack(">I", size)
        + bytes(4)
        + struct.pack(">I", 0x02100000)
        + b"mntrRGB XYZ "
        + bytes(12)
        + b"acsp"
        + bytes(24)
        + struct.pack(">I", 0)
        + _s15f16(_D50[0])
        + _s15f16(_D50[1])
        + _s15f16(_D50[2])
        + bytes(48)
    )
    assert len(header) == 128
    return header + table + data


def display_p3_like_profile() -> bytes:
    return matrix_profile(DISPLAY_P3_D50)
