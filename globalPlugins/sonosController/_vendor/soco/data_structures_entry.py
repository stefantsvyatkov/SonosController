"""This module is for parsing and conversion functions that needs
objects from both music library and music service data structures

"""

from functools import lru_cache
import logging
import re
from xml.etree import ElementTree as ET

from .data_structures import didl_class_to_soco_class
from .exceptions import DIDLMetadataError
from .xml import ns_tag

_LOG = logging.getLogger(__name__)
_LOG.addHandler(logging.NullHandler())
_LOG.debug("%s imported", __name__)

_BARE_AMPERSAND = re.compile(r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z_][\w.:-]*;)")

_NUMERIC_REFERENCE = re.compile(r"&#(?:(\d+)|x([0-9A-Fa-f]+));")

def _valid_xml_codepoint(codepoint):
    return (
        codepoint in (9, 10, 13)
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )

def _clean_numeric_reference(match):
    codepoint = int(match.group(1) or match.group(2), 10 if match.group(1) else 16)
    return match.group(0) if _valid_xml_codepoint(codepoint) else ""

def _clean_xml_text(value):
    """Repair the two malformed DIDL patterns seen from Sonos services."""
    value = "".join(
        character for character in value
        if _valid_xml_codepoint(ord(character))
    )
    value = _NUMERIC_REFERENCE.sub(_clean_numeric_reference, value)
    return _BARE_AMPERSAND.sub("&amp;", value)

def _parse_xml(value):
    try:
        return ET.fromstring(value.encode("utf-8"))
    except (ET.ParseError, UnicodeEncodeError) as first_error:
        cleaned = _clean_xml_text(value)
        if cleaned == value:
            raise DIDLMetadataError("Could not parse DIDL-Lite metadata") from first_error
        try:
            return ET.fromstring(cleaned.encode("utf-8"))
        except (ET.ParseError, UnicodeEncodeError) as error:
            raise DIDLMetadataError("Could not parse DIDL-Lite metadata") from error



@lru_cache()
def from_didl_string(string):
    """Convert a unicode xml string to a list of `DIDLObjects <DidlObject>`.

    Args:
        string (str): A unicode string containing an XML representation of one
            or more DIDL-Lite items (in the form  ``'<DIDL-Lite ...>
            ...</DIDL-Lite>'``)

    Returns:
        list: A list of one or more instances of `DidlObject` or a subclass
    """
    items = []
    root = _parse_xml(string)
    for elt in root:
        if elt.tag.endswith("item") or elt.tag.endswith("container"):
            item_class = elt.findtext(ns_tag("upnp", "class"))
            cls = didl_class_to_soco_class(item_class)
            item = cls.from_element(elt)
            items.append(item)
        else:
            # <desc> elements are allowed as an immediate child of <DIDL-Lite>
            # according to the spec, but I have not seen one there in Sonos, so
            # we treat them as illegal. May need to fix this if this
            # causes problems.
            raise DIDLMetadataError("Illegal child of DIDL element: <%s>" % elt.tag)
    _LOG.debug(
        'Created data structures: %.20s (CUT) from Didl string "%.20s" (CUT)',
        items,
        string,
    )
    return items
