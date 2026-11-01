#!/usr/bin/env python3
"""Write a minimal CBZ (one 1x1 PNG page plus a ComicInfo.xml) for the kavita role's scan test.

Usage: make-test-cbz.py <output.cbz> <series name> <volume number>

Kept dependency-free (zlib, struct, zipfile) so it runs on the target's system Python. The ComicInfo.xml
mirrors the library convention in the role README: Series, Volume and an AgeRating of Everyone.
"""

import struct
import sys
import zipfile
import zlib


def png_1x1() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    # One scanline: filter byte 0, then one RGB pixel.
    idat = zlib.compress(b"\x00\xff\xff\xff")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def main() -> None:
    output, series, volume = sys.argv[1], sys.argv[2], sys.argv[3]
    comic_info = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<ComicInfo>\n"
        f"  <Series>{series}</Series>\n"
        f"  <Volume>{volume}</Volume>\n"
        "  <AgeRating>Everyone</AgeRating>\n"
        "</ComicInfo>\n"
    )
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as cbz:
        cbz.writestr("ComicInfo.xml", comic_info)
        cbz.writestr("001.png", png_1x1())


if __name__ == "__main__":
    main()
