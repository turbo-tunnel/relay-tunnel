# -*- coding: utf-8 -*-

"""Turbo Relay Tunnel
"""

import traceback

VERSION = "0.1.3"

try:
    from . import websocket
except ImportError:
    traceback.print_exc()
