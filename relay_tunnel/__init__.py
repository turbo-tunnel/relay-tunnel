# -*- coding: utf-8 -*-

"""Turbo Relay Tunnel
"""

import traceback

VERSION = "0.1.0"

try:
    from . import websocket
except ImportError:
    traceback.print_exc()
