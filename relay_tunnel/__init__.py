# -*- coding: utf-8 -*-

"""Turbo Relay Tunnel
"""

import traceback

VERSION = "0.3.1"

try:
    from . import http
    from . import irc
    from . import websocket
except ImportError:
    traceback.print_exc()
