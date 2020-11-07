# -*- coding: utf-8 -*-

import socket

import pytest
import turbo_tunnel

from relay_tunnel import http

from .util import DemoTCPServer, get_random_port


async def test_auth():
    relay_server_port = get_random_port()
    listen_url = "http+relay://test:test@127.0.0.1:%d/relay" % relay_server_port
    relay_server = http.HTTPRelayTunnelServer(listen_url, ["tcp://"])
    relay_server.start()

    s = socket.socket()
    tunn = turbo_tunnel.tunnel.TCPTunnel(s, address=("127.0.0.1", relay_server_port))
    await tunn.connect()

    url = turbo_tunnel.utils.Url(
        "http+relay://test:test2@127.0.0.1:%d/relay?client_id=test1" % relay_server_port
    )
    relay_tunnel = http.HTTPRelayTunnel(tunn, url)
    assert not await relay_tunnel.connect()

    url = turbo_tunnel.utils.Url(
        "http+relay://test:test@127.0.0.1:%d/relay" % relay_server_port
    )
    relay_tunnel = http.HTTPRelayTunnel(tunn, url)
    assert await relay_tunnel.connect()


async def test_tunnel():
    server = DemoTCPServer()
    port = get_random_port()
    server.listen(port)

    relay_server_port = get_random_port()
    listen_url = "http+relay://127.0.0.1:%d/relay" % relay_server_port
    relay_server = http.HTTPRelayTunnelServer(listen_url, ["tcp://"])
    relay_server.start()

    s = socket.socket()
    tunn1 = turbo_tunnel.tunnel.TCPTunnel(s, address=("127.0.0.1", relay_server_port))
    await tunn1.connect()
    url1 = turbo_tunnel.utils.Url(listen_url + "?client_id=123")
    relay_tunnel1 = http.HTTPRelayTunnel(tunn1, url1)
    assert await relay_tunnel1.connect()

    s = socket.socket()
    tunn2 = turbo_tunnel.tunnel.TCPTunnel(s, address=("127.0.0.1", relay_server_port))
    await tunn2.connect()
    url2 = turbo_tunnel.utils.Url(listen_url + "?client_id=456&target_id=123")
    relay_tunnel2 = http.HTTPRelayTunnel(tunn2, url2, address=("127.0.0.1", port))
    assert await relay_tunnel2.connect()

    await relay_tunnel2.write(b"Hello python!\n")
    assert await relay_tunnel2.read() == b"Hello python!\n"
