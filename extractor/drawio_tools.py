"""
DrawIO encoding/decoding utilities.
Handles compressed base64 XML format used by draw.io.
"""

import base64
import zlib
from urllib.parse import quote, unquote


def pako_deflate_raw(data):
    """Compress data using raw deflate (no zlib header)."""
    compress = zlib.compressobj(
        zlib.Z_DEFAULT_COMPRESSION,
        zlib.DEFLATED,
        -15,
        memLevel=8,
        strategy=zlib.Z_DEFAULT_STRATEGY
    )
    compressed_data = compress.compress(data)
    compressed_data += compress.flush()
    return compressed_data


def pako_inflate_raw(data):
    """Decompress raw deflate data."""
    decompress = zlib.decompressobj(-15)
    decompressed_data = decompress.decompress(data)
    decompressed_data += decompress.flush()
    return decompressed_data


def decode_diagram_data(data):
    """
    Decode compressed base64 drawio diagram content.

    Args:
        data: Base64 encoded, deflate compressed, URL-encoded XML string

    Returns:
        Decoded XML string, or None if decoding fails
    """
    try:
        data = base64.b64decode(data)
        data = pako_inflate_raw(data)
        data = data.decode('utf-8')
        data = unquote(data)
        return data
    except Exception:
        return None


def encode_diagram_data(data):
    """
    Encode diagram data for storage in drawio format.

    Args:
        data: XML string to encode

    Returns:
        Encoded bytes (base64)
    """
    data = quote(data, safe="~()*!.'")
    data = data.encode()
    data = pako_deflate_raw(data)
    data = base64.b64encode(data)
    return data
