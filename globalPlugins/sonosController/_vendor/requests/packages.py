"""Compatibility aliases contained entirely inside the private namespace."""
import sys

from .. import idna, urllib3
from .compat import chardet

def _alias(package, public_name):
    prefix = package.__name__
    for module_name, module in list(sys.modules.items()):
        if module_name == prefix or module_name.startswith(prefix + '.'):
            suffix = module_name[len(prefix):]
            sys.modules[f'{__name__}.{public_name}{suffix}'] = module

_alias(urllib3, 'urllib3')
_alias(idna, 'idna')
_alias(chardet, 'charset_normalizer')
_alias(chardet, 'chardet')
