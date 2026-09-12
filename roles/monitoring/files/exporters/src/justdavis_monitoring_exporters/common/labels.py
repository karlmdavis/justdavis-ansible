"""Sanitising of label values that originate from the network (hostnames, friendly names, mDNS)."""

MAX_LABEL_LENGTH = 64

_QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})  # noqa: RUF001


def sanitise_label(value: str, max_length: int = MAX_LABEL_LENGTH) -> str:
    """Keep printable ASCII only (curly quotes become straight ones), trimmed and length-capped."""
    ascii_only = "".join(ch for ch in value.translate(_QUOTE_MAP) if 32 <= ord(ch) < 127)
    return ascii_only.strip()[:max_length]
