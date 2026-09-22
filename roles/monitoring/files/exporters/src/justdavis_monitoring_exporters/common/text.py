"""Decoding of device responses for the parsers and login-page checks."""


def as_text(body: bytes | str) -> str:
    """Device pages as text; undecodable bytes are replaced rather than failing the parse."""
    return body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
