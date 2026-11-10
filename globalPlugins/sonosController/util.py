"""Small helpers shared across the add-on."""

from urllib.parse import urlsplit

#: A station still buffering is playing as far as the listener is concerned:
#: radio takes seconds to start, and a room that has just been given music
#: must not be refused as silent.
PLAYING = ("PLAYING", "TRANSITIONING")

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


class Played(str):
    """What is said when playback starts, carrying the title that started.

    It is still the plain message everywhere it is spoken or compared; the
    layer reads ``name`` to name the history entry (``title`` is a str method).
    """

    def __new__(cls, message, name):
        value = super().__new__(cls, message)
        value.name = name
        return value


def readable(text):
    """Text a station sent as bytes in its own encoding, read back as it was meant.

    HTTP headers, and a speaker reporting a stream's name, take those bytes as
    Latin-1: "Радио" in UTF-8 becomes "Ð Ð°Ð´Ð¸Ð¾", and in Windows-1251, which
    older Bulgarian stations still send, "Ðàäèî".
    """
    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError:
        return text
    if raw.isascii():
        return text
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # Windows-1251 only when most letters were mangled, so "Café" stays.
    letters = [c for c in text if c.isalpha()]
    mangled = [c for c in letters if not c.isascii()]
    if len(mangled) >= 3 and len(mangled) * 2 > len(letters):
        return raw.decode("cp1251", errors="replace")
    return text


def timestamp(total):
    """Seconds as ``H:MM:SS``."""
    return f"{total // 3600}:{total // 60 % 60:02}:{total % 60:02}"


def transient(error):
    """True when a Sonos failure may simply be a slow or dropped response."""
    return (str(getattr(error, "error_code", "")) == "701"
            or isinstance(error, (TimeoutError, ConnectionError))
            or "Timeout" in type(error).__name__
            or "ConnectionError" in type(error).__name__)
