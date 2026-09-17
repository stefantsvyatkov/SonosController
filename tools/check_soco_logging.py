"""Regression check for the missing xml.dom module in frozen NVDA."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "globalPlugins/sonosController"))
from _vendor.soco.services import prettify as service_prettify
from _vendor.soco.soap import prettify as soap_prettify

with patch.dict(sys.modules, {"xml.dom": None, "xml.dom.minidom": None}):
    for formatter in (service_prettify, soap_prettify):
        assert formatter('<test>Music</test>') == '<test>Music</test>'
        assert formatter(b'<test>Music</test>') == '<test>Music</test>'
print("SoCo logging works without xml.dom in both SOAP paths.")
