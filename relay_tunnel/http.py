# -*- coding: utf-8 -*-

"""HTTP Relay Tunnel
"""

import asyncio
import copy
import socket
import time

import tornado.httpclient
import tornado.simple_httpclient
import tornado.tcpclient
import turbo_tunnel

from . import utils


class HTTPTunnel(turbo_tunnel.tunnel.Tunnel):
    """HTTP Tunnel"""

    def __init__(self, tunnel, url, address=None):
        super(HTTPTunnel, self).__init__(tunnel, url, address)
        self._token = self._url.params.get("token")
        self._access_key = None
        self._buffer = b""
        self._tunnel_pool = []
        self._read_event = asyncio.Event()

    async def _get_tunnel(self):
        tunnel = None
        while tunnel is None:
            if not self._tunnel_pool:
                tunnel = await self._tunnel.fork()
                if tunnel is None:
                    turbo_tunnel.utils.logger.warn(
                        "[%s] Fork %s failed, retry later"
                        % (self.__class__.__name__, self._tunnel)
                    )
                    await asyncio.sleep(10)
            else:
                tunnel = self._tunnel_pool.pop(0)
                if tunnel.closed():
                    tunnel = None
        return tunnel

    def _put_tunnel(self, tunnel):
        self._tunnel_pool.append(tunnel)

    def _patch_tcp_client(self, tunn):
        TCPClient = tornado.tcpclient.TCPClient

        async def connect(
            tcp_client,
            host,
            port,
            af=socket.AF_UNSPEC,
            ssl_options=None,
            max_buffer_size=None,
            source_ip=None,
            source_port=None,
            timeout=None,
        ):
            tun = tunn
            if ssl_options is not None:
                url = turbo_tunnel.utils.Url(self._url)
                tun = turbo_tunnel.tunnel.SSLTunnel(
                    tun,
                    sslcontext=ssl_options,
                    verify_ssl=url.params.get("verify_ssl") != "false",
                    server_hostname=url.params.get("server_hostname", host),
                )
                await tun.connect()
            stream = turbo_tunnel.tunnel.TunnelIOStream(tun)
            return stream

        class TCPClientPatchContext(object):
            def __init__(self, patched_connect):
                self._origin_connect = TCPClient.connect
                self._patched_connect = patched_connect

            def patch(self):
                TCPClient.connect = self._patched_connect

            def unpatch(self):
                TCPClient.connect = self._origin_connect

            def __enter__(self):
                self.patch()

            def __exit__(self, exc_type, exc_value, exc_trackback):
                self.unpatch()

        return TCPClientPatchContext(connect)

    async def connect(self):
        if not self._access_key:
            try:
                await self.post_request()
            except utils.HTTPResponseError as e:
                turbo_tunnel.utils.logger.warn(
                    "[%s] Connect http relay tunnel %s failed: [%d] %s"
                    % (self.__class__.__name__, self._url, e.code, e.message)
                )
                return False
            turbo_tunnel.utils.AsyncTaskManager().start_task(self._reading_task())
        return True

    async def post_request(self, params=None, headers=None, body=None):
        http_client = tornado.simple_httpclient.SimpleAsyncHTTPClient()
        url = copy.deepcopy(self._url)
        if params:
            for param in params:
                url.params[param] = params[param]
        url = str(url)
        headers = headers or {}
        headers["Connection"] = "Keep-Alive"
        headers["Content-Type"] = "application/octet-stream"
        auth_data = self._url.auth
        if auth_data:
            headers[
                "Proxy-Authorization"
            ] = "Basic %s" % turbo_tunnel.auth.http_basic_auth(*auth_data.split(":"))
        if self._access_key:
            headers["X-Access-Key"] = self._access_key
        body = body or b""
        tunnel = await self._get_tunnel()
        assert tunnel is not None

        with self._patch_tcp_client(tunnel) as context:
            request = tornado.httpclient.HTTPRequest(
                url, "POST", headers=headers, body=body
            )
            try:
                response = await http_client.fetch(request)
            except tornado.httpclient.HTTPClientError as e:
                if e.code == 599:
                    raise turbo_tunnel.utils.TunnelClosedError()
                else:
                    raise utils.HTTPResponseError(e.code, e.message)

            if "X-Access-Key" in response.headers:
                # Update access key
                self._access_key = response.headers["X-Access-Key"]

            self._put_tunnel(tunnel)
            return response.body

    async def retry_post_request(self, params=None, headers=None, body=None):
        while True:
            try:
                return await self.post_request(params, headers, body)
            except turbo_tunnel.utils.TunnelClosedError:
                turbo_tunnel.utils.logger.warn(
                    "[%s] HTTP tunnel %s closed, retry later"
                    % (self.__class__.__name__, self._url)
                )
                await asyncio.sleep(10)

    async def _reading_task(self):
        timeout = 10
        while True:
            buffer = await self.retry_post_request({"timeout": timeout})
            if buffer:
                self._buffer += buffer
                self._read_event.set()

    async def write_packet(self, packet):
        buffer = packet.serialize()
        await self.retry_post_request(
            params={"target_id": packet.receiver}, body=buffer
        )

    async def read_packet(self):
        while True:
            if self._buffer:
                packet, self._buffer = utils.RelayPacket.parse(self._buffer, self._token)
                if packet:
                    return packet

            await self._read_event.wait()
            self._read_event.clear()

    def closed(self):
        return False


class HTTPRelayTunnel(utils.RelayTunnel):
    """HTTP Relay Tunnel"""

    underlay_tunnel_class = HTTPTunnel


class HTTPRelayTunnelServer(turbo_tunnel.server.TunnelServer):
    """HTTP Relay Tunnel Server"""

    keepalive_timeout = 60

    def post_init(self):
        this = self
        self._clients = {}

        class HTTPRelayHandler(tornado.web.RequestHandler):
            """HTTP Relay Handler"""

            SUPPORTED_METHODS = ["POST"]

            async def post(self):
                client_id = self.request.arguments.get("client_id")
                if not client_id:
                    self.set_status(400, "Bad Request")
                    return False
                client_id = client_id[0].decode()
                target_id = self.request.arguments.get("target_id")
                if target_id:
                    target_id = target_id[0].decode()
                timeout = self.request.arguments.get("timeout")
                if timeout:
                    timeout = float(timeout[0])
                auth_data = this._listen_url.auth
                if auth_data:
                    auth_data = auth_data.split(":")
                    value = self.request.headers.get("Proxy-Authorization")
                    auth_success = False
                    if value:
                        auth_type, auth_value = value.split()
                        if (
                            auth_type == "Basic"
                            and auth_value
                            == turbo_tunnel.auth.http_basic_auth(*auth_data)
                        ):
                            auth_success = True
                    if not auth_success:
                        turbo_tunnel.utils.logger.info(
                            "[%s] Client %s join refused due to wrong auth"
                            % (self.__class__.__name__, client_id)
                        )
                        self.set_status(403, "Forbidden")
                        return False

                if client_id not in this._clients:
                    access_key = utils.create_random_string()
                    this._clients[client_id] = {
                        "access_key": access_key,
                        "messages": {},
                        "last_active_time": time.time(),
                    }
                    self.set_header("X-Access-Key", access_key)
                    turbo_tunnel.utils.logger.info(
                        "[%s] New client %s joined"
                        % (self.__class__.__name__, client_id)
                    )
                else:
                    # Check access key
                    access_key = self.request.headers.get("X-Access-Key")
                    if access_key != this._clients[client_id]["access_key"]:
                        turbo_tunnel.utils.logger.warn(
                            "[%s] Invalid access of %s "
                            % (self.__class__.__name__, client_id)
                        )
                        self.set_status(400, "Bad Request")
                        return False
                    this._clients[client_id]["last_active_time"] = time.time()

                if target_id and self.request.body:
                    if target_id not in this._clients:
                        turbo_tunnel.utils.logger.warn(
                            "[%s] Invalid target %s "
                            % (self.__class__.__name__, target_id)
                        )
                        self.set_status(400, "Bad Request")
                        return False
                    if client_id not in this._clients[target_id]["messages"]:
                        this._clients[target_id]["messages"][client_id] = b""

                    this._clients[target_id]["messages"][client_id] += self.request.body
                    turbo_tunnel.utils.logger.debug(
                        "[%s] Transfer %d bytes from %s to %s"
                        % (
                            self.__class__.__name__,
                            len(self.request.body),
                            client_id,
                            target_id,
                        )
                    )

                if self.request.body:
                    return

                time0 = time.time()
                while not timeout or time.time() - time0 < timeout:
                    messages = this._clients[client_id]["messages"]
                    keys = list(messages.keys())
                    for source in keys:
                        buffer = messages.pop(source)
                        packet, buffer = utils.RelayPacket.parse(buffer)
                        messages[source] = buffer
                        if packet:
                            self.write(packet.serialize())
                            return
                    if not timeout:
                        break
                    await asyncio.sleep(0.01)

        handlers = [
            (self._listen_url.path, HTTPRelayHandler),
        ]
        self._app = tornado.web.Application(handlers)

    async def _check_client_task(self):
        while True:
            now = time.time()
            for client in self._clients:
                if (
                    now - self._clients[client]["last_active_time"]
                    >= self.keepalive_timeout
                ):
                    turbo_tunnel.utils.logger.warn(
                        "[%s] Client %s heartbeat timeout, kick out"
                        % (self.__class__.__name__, client)
                    )
                    self._clients.pop(client)
                    break
            await asyncio.sleep(1)

    def start(self):
        self._app.listen(self._listen_url.port, self._listen_url.host)
        turbo_tunnel.utils.logger.info(
            "[%s] HTTP relay server is listening on %s:%d"
            % (self.__class__.__name__, self._listen_url.host, self._listen_url.port)
        )
        turbo_tunnel.utils.AsyncTaskManager().start_task(self._check_client_task())


turbo_tunnel.registry.tunnel_registry.register("http+relay", HTTPRelayTunnel)
turbo_tunnel.registry.server_registry.register("http+relay", HTTPRelayTunnelServer)
turbo_tunnel.registry.tunnel_registry.register("https+relay", HTTPRelayTunnel)
turbo_tunnel.registry.server_registry.register("https+relay", HTTPRelayTunnelServer)
