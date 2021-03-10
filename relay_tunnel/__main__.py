# -*- coding: utf-8 -*-

import asyncio
import argparse
import logging
import sys

import tornado
import turbo_tunnel

from . import http
from . import irc
from . import websocket


async def create_relay_tunnel(tunnel_urls, relay_url, retry):
    while True:
        tunnel_chain = turbo_tunnel.chain.TunnelChain(tunnel_urls, retry)
        try:
            await tunnel_chain.create_tunnel(relay_url.address)
        except turbo_tunnel.utils.TunnelConnectError:
            turbo_tunnel.utils.logger.warn(
                "Connect relay tunnel failed, retry later..."
            )
            await asyncio.sleep(5)
            continue
        tunnel = tunnel_chain.tail
        protocol = relay_url.protocol.replace("+relay", "")
        if protocol in ("ws", "wss"):
            relay_tunnel = websocket.WebSocketRelayTunnel(tunnel, relay_url)
        elif protocol in ('http', 'https'):
            relay_tunnel = http.HTTPRelayTunnel(tunnel, relay_url)
        elif protocol == 'irc':
            relay_tunnel = irc.IRCRelayTunnel(tunnel, relay_url)
        else:
            raise NotImplementedError(protocol)
        if not await relay_tunnel.connect():
            turbo_tunnel.utils.logger.error(
                "Connect relay tunnel %s failed" % relay_url
            )
            break
        turbo_tunnel.utils.logger.info("Relay tunnel connected, waiting for client...")
        await relay_tunnel.wait_until_closed()
        turbo_tunnel.utils.logger.warn("Relay tunnel closed, retry connecting...")
        await asyncio.sleep(1)


def main():
    parser = argparse.ArgumentParser(
        prog="relay-tunnel", description="RelayTunnel cmdline tool."
    )
    parser.add_argument("-t", "--tunnel", action="append", help="tunnel url")
    parser.add_argument("-s", "--server", help="relay server url", required=True)
    parser.add_argument("--retry", type=int, help="retry connect count", default=0)
    parser.add_argument(
        "--log-level",
        help="log level, default is info",
        choices=("debug", "info", "warn", "error"),
        default="info",
    )
    parser.add_argument(
        "-d", "--daemon", help="run as daemon", action="store_true", default=False
    )
    args = parser.parse_args()

    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s][%(levelname)s]%(message)s")
    handler.setFormatter(formatter)
    if args.log_level == "debug":
        turbo_tunnel.utils.logger.setLevel(logging.DEBUG)
    elif args.log_level == "info":
        turbo_tunnel.utils.logger.setLevel(logging.INFO)
    elif args.log_level == "warn":
        turbo_tunnel.utils.logger.setLevel(logging.WARN)
    elif args.log_level == "error":
        turbo_tunnel.utils.logger.setLevel(logging.ERROR)
    turbo_tunnel.utils.logger.propagate = 0
    turbo_tunnel.utils.logger.addHandler(handler)

    if sys.platform != "win32" and args.daemon:
        import daemon

        daemon.DaemonContext(stderr=open("error.txt", "w")).open()
    elif args.daemon:
        utils.win32_daemon()
        return 0

    relay_url = turbo_tunnel.utils.Url(args.server)
    tunnel_urls = args.tunnel
    if not tunnel_urls:
        tunnel_urls = ["tcp://"]

    turbo_tunnel.utils.AsyncTaskManager().start_task(
        create_relay_tunnel(
            [turbo_tunnel.utils.Url(url) for url in tunnel_urls],
            relay_url,
            args.retry + 1,
        )
    )

    if sys.platform == 'win32' and sys.version_info[1] >= 8:
        # on Windows, the default asyncio event loop is ProactorEventLoop from python3.8
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)

    try:
        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        print("Process exit warmly.")


if __name__ == "__main__":
    sys.exit(main())
