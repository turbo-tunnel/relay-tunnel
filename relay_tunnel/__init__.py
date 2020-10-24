# -*- coding: utf-8 -*-

"""Turbo Relay Tunnel
"""

import traceback

VERSION = "0.2.0"

try:
    from . import http
    from . import websocket
except ImportError:
    traceback.print_exc()
