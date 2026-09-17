"""Small helpers shared across the add-on."""

from urllib.parse import urlsplit

#: How far Page Up and Page Down move any value the arrows step by one.
#: Volume is in percent, so this is five percentage points there.
PAGE_STEP = 5


def valid_url(text):
    """True for a plain HTTP or HTTPS address with no embedded credentials."""
    text = str(text or "").strip()
    if any(c.isspace() for c in text):
        return False
    try:
        parts = urlsplit(text)
        return parts.scheme.lower() in ("http", "https") and bool(parts.hostname) and not parts.username
    except ValueError:
        return False


def timestamp(total):
    """Seconds as ``H:MM:SS``."""
    return f"{total // 3600}:{total // 60 % 60:02}:{total % 60:02}"


def transient(error):
    """True when a Sonos failure may simply be a slow or dropped response."""
    return (str(getattr(error, "error_code", "")) == "701"
            or isinstance(error, (TimeoutError, ConnectionError))
            or "Timeout" in type(error).__name__
            or "ConnectionError" in type(error).__name__)
