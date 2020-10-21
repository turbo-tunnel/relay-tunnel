# -*- coding: utf-8 -*-

"""WebSocket Relay Tunnel
"""

import asyncio
import copy

import tornado.web
import tornado.websocket
import turbo_tunnel

from . import utils


class WebSocketRelayTunnel(turbo_tunnel.tunnel.Tunnel):
    """WebSocket Relay Tunnel"""

    transports = {}

    def __init__(self, tunnel, url, address=None):
        target_id = url.params.get("target_id")
        client_id = url.params.get("client_id")
        token = url.params.get("token")
        url = copy.copy(url)
        url.protocol = url.protocol.replace("+relay", "")
        if not client_id:
            client_id = utils.create_random_string(exclude="?&")
            url.params["client_id"] = client_id

        address = address or (None, None)
        tunnel = turbo_tunnel.websocket.WebSocketTunnel(tunnel, url, address)
        super(WebSocketRelayTunnel, self).__init__(tunnel, url, address)
        if str(url) in self.__class__.transports:
            self._relay_transport = self.__class__.transports[str(url)]
            self._connected = True
        else:
            self._relay_transport = utils.RelayTransport(
                self._tunnel, client_id, target_id, token
            )
            self.__class__.transports[str(url)] = self._relay_transport
            self._connected = False
        self._stream = None

    @classmethod
    def has_cache(cls, url):
        key = str(url).replace("+relay", "")
        if key in cls.transports:
            transport = cls.transports[key]
            if transport.closed():
                turbo_tunnel.utils.logger.warn(
                    "[%s] WebSocket connection %s closed, remove cache"
                    % (cls.__name__, key)
                )
                cls.transports.pop(key)
                return False
            return True
        return False

    async def connect(self):
        if not self._connected:
            if not await self._tunnel.connect():
                return False
            else:
                self._relay_transport.start_transport()
        self._connected = True

        if self._addr != self._url.host or self._port != self._url.port:
            self._stream = await self._relay_transport.create_stream(
                (self._addr, self._port)
            )
            return self._stream is not None
        return True

    async def read(self):
        try:
            return await self._stream.read()
        except utils.StreamClosedError:
            raise turbo_tunnel.utils.TunnelClosedError()

    async def write(self, buffer):
        return await self._stream.write(buffer)

    def close(self):
        if self._stream:
            self._stream.close()
            self._stream = None

    async def wait_until_closed(self):
        while not self._relay_transport.closed():
            await asyncio.sleep(1)
        self.__class__.transports.pop(str(self._url))


class WebSocketRelayTunnelServer(turbo_tunnel.server.TunnelServer):
    """WebSocket Relay Tunnel Server"""

    def post_init(self):
        this = self
        self._clients = {}

        class WebSocketProtocol(tornado.websocket.WebSocketProtocol13):
            async def accept_connection(self, handler):
                if await self.handler.accept_connection():
                    await super(WebSocketProtocol, self).accept_connection(handler)

        class WebSocketRelayHandler(tornado.websocket.WebSocketHandler):
            """WebSocket Relay Handler"""

            def __init__(self, *args, **kwargs):
                super(WebSocketRelayHandler, self).__init__(*args, **kwargs)
                self._client_id = None
                self._tun_conn = None

            async def accept_connection(self):
                client_id = self.request.arguments.get("client_id")
                if not client_id:
                    self.set_status(400, "Bad Request")
                    return False
                self._client_id = client_id[0].decode()
                auth_data = this._listen_url.auth
                if auth_data:
                    auth_data = auth_data.split(":")
                    for header in self.request.headers:
                        if header == "Proxy-Authorization":
                            value = self.request.headers[header]
                            auth_type, auth_value = value.split()
                            if (
                                auth_type == "Basic"
                                and auth_value
                                == turbo_tunnel.auth.http_basic_auth(*auth_data)
                            ):
                                break
                    else:
                        turbo_tunnel.utils.logger.info(
                            "[%s] Client %s join refused due to wrong auth"
                            % (self.__class__.__name__, self._client_id)
                        )
                        self.set_status(403, "Forbidden")
                        return False

                if self._client_id in this._clients:
                    turbo_tunnel.utils.logger.warn(
                        "[%s] Client %s already joined"
                        % (self.__class__.__name__, self._client_id)
                    )
                    self.set_status(400, "Bad Request")
                    return False
                this._clients[
                    self._client_id
                ] = turbo_tunnel.websocket.WebSocketDownStream(self)
                turbo_tunnel.utils.AsyncTaskManager().start_task(
                    this.forward_stream(self._client_id)
                )
                turbo_tunnel.utils.logger.info(
                    "[%s] New client %s joined"
                    % (self.__class__.__name__, self._client_id)
                )
                return True

            def get_websocket_protocol(self):
                """Override to accept connection"""
                websocket_version = self.request.headers.get("Sec-WebSocket-Version")
                if websocket_version in ("7", "8", "13"):
                    params = tornado.websocket._WebSocketParams(
                        ping_interval=self.ping_interval,
                        ping_timeout=self.ping_timeout,
                        max_message_size=self.max_message_size,
                        compression_options=self.get_compression_options(),
                    )
                    return WebSocketProtocol(self, mask_outgoing=True, params=params)

            async def on_message(self, message):
                this._clients[self._client_id].on_recv(message)

            def on_connection_close(self):
                super(WebSocketRelayHandler, self).on_connection_close()
                this._clients[self._client_id].close()

        handlers = [
            (self._listen_url.path, WebSocketRelayHandler),
        ]
        self._app = tornado.web.Application(
            handlers, websocket_ping_interval=10, websocket_ping_timeout=30
        )

    async def forward_stream(self, client_id):
        stream = self._clients.get(client_id)
        if not stream:
            return

        buffer = b""
        while True:
            try:
                buffer += await stream.read()
            except turbo_tunnel.utils.TunnelClosedError:
                turbo_tunnel.utils.logger.warn(
                    "[%s] Client %s leaved" % (self.__class__.__name__, client_id)
                )
                self._clients.pop(client_id)
                break

            while buffer:
                packet, buffer = utils.RelayPacket.parse(buffer)
                if not packet:
                    break
                if packet.sender != client_id:
                    turbo_tunnel.utils.logger.warn(
                        "[%s] Invalid sender: %s"
                        % (self.__class__.__name__, packet.sender)
                    )
                    continue
                if packet.receiver not in self._clients:
                    turbo_tunnel.utils.logger.warn(
                        "[%s] Invalid receiver: %s"
                        % (self.__class__.__name__, packet.receiver)
                    )
                    # TODO: Send Fail
                    continue

                try:
                    await self._clients[packet.receiver].write(packet.serialize())
                except turbo_tunnel.utils.TunnelClosedError:
                    turbo_tunnel.utils.logger.warn(
                        "[%s] Client %s closed"
                        % (self.__class__.__name__, packet.receiver)
                    )
                    self._clients.pop(packet.receiver)
                    # TODO: Send notify
                    continue

    def start(self):
        self._app.listen(self._listen_url.port, self._listen_url.host)
        turbo_tunnel.utils.logger.info(
            "[%s] WebSocket relay server is listening on %s:%d"
            % (self.__class__.__name__, self._listen_url.host, self._listen_url.port)
        )


turbo_tunnel.registry.tunnel_registry.register("ws+relay", WebSocketRelayTunnel)
turbo_tunnel.registry.server_registry.register("ws+relay", WebSocketRelayTunnelServer)
turbo_tunnel.registry.tunnel_registry.register("wss+relay", WebSocketRelayTunnel)
turbo_tunnel.registry.server_registry.register("wss+relay", WebSocketRelayTunnelServer)
