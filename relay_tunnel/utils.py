# -*- coding: utf-8 -*-

"""Miscellaneous utility functions and classes
"""

import asyncio
import copy
import random
import struct
import time

import turbo_tunnel


class RelayPacketError(RuntimeError):
    pass


class StreamStatusError(RuntimeError):
    pass


class StreamClosedError(RuntimeError):
    pass


class HTTPResponseError(RuntimeError):
    def __init__(self, code, message=None):
        self._code = code
        self._message = message

    @property
    def code(self):
        return self._code

    @property
    def message(self):
        return self._message


class EnumStreamEvent(object):
    OPEN = b"OPEN"
    OKAY = b"OKAY"
    FAIL = b"FAIL"
    WRITE = b"WRTE"
    CLOSE = b"CLSE"
    PING = b"PING"
    PONG = b"PONG"


class EnumStreamStatus(object):
    UNINITIALIZED = 0
    OPENING = 1
    ESTABLISHED = 2
    CLOSING = 3
    CLOSED = 4
    PINGING = 5


class RelayPacket(object):
    def __init__(self, sender, receiver, body):
        self._sender = sender if isinstance(sender, bytes) else sender.encode()
        if len(self._sender) > 255:
            raise RuntimeError("Sender is too long")
        self._receiver = receiver if isinstance(receiver, bytes) else receiver.encode()
        if len(self._receiver) > 255:
            raise RuntimeError("Receiver is too long")
        assert isinstance(body, bytes)
        self._body = body

    @property
    def sender(self):
        return self._sender.decode()

    @property
    def receiver(self):
        return self._receiver.decode()

    @property
    def body(self):
        return self._body

    def serialize(self):
        total_size = (
            4 + 1 + len(self._sender) + 1 + len(self._receiver) + len(self._body)
        )
        buffer = struct.pack("!I", total_size)
        buffer += struct.pack("B", len(self._sender)) + self._sender
        buffer += struct.pack("B", len(self._receiver)) + self._receiver
        buffer += self._body
        return buffer

    @staticmethod
    def parse(buffer):
        assert isinstance(buffer, bytes)
        if len(buffer) < 4:
            return None, buffer
        total_size = struct.unpack("!I", buffer[:4])[0]
        if len(buffer) < total_size:
            return None, buffer
        offset = 4
        sender_size = struct.unpack("B", buffer[offset : offset + 1])[0]
        offset += 1
        sender = buffer[offset : offset + sender_size]
        offset += sender_size
        receiver_size = struct.unpack("B", buffer[offset : offset + 1])[0]
        offset += 1
        receiver = buffer[offset : offset + receiver_size]
        offset += receiver_size
        body = buffer[offset:total_size]
        buffer = buffer[total_size:]
        return RelayPacket(sender, receiver, body), buffer


class StreamPacket(object):
    def __init__(self, stream_id, event, **kwargs):
        self._stream_id = stream_id
        self._event = event
        self._params = kwargs

    @property
    def stream_id(self):
        return self._stream_id

    @property
    def event(self):
        return self._event

    def __getattr__(self, attr):
        if attr in self._params:
            return self._params[attr]
        raise AttributeError(attr)

    def serialize(self):
        buffer = struct.pack("!I", self._stream_id)
        buffer += self._event
        if self._event == EnumStreamEvent.OPEN:
            target_addr = self._params["addr"]
            if not isinstance(target_addr, bytes):
                target_addr = target_addr.encode()
            target_addr = target_addr[:255]  # address support max 255 bytes
            target_port = self._params["port"]
            token = self._params.get("token") or ""
            if not isinstance(token, bytes):
                token = token.encode()
            token = token[:255]  # token support max 255 bytes
            buffer += struct.pack("B", len(target_addr))
            buffer += target_addr
            buffer += struct.pack("!H", target_port)
            buffer += struct.pack("B", len(token))
            buffer += token
        elif self._event == EnumStreamEvent.OKAY:
            pass
        elif self._event == EnumStreamEvent.FAIL:
            reason = self._params.get("reason") or ""
            if not isinstance(reason, bytes):
                reason = reason.encode()
            reason = reason[:255]
            buffer += struct.pack("B", len(reason))
            buffer += reason
        elif self._event == EnumStreamEvent.WRITE:
            data = self._params["data"]
            buffer += data
        elif self._event == EnumStreamEvent.CLOSE:
            pass
        elif self._event in (EnumStreamEvent.PING, EnumStreamEvent.PONG):
            pass
        else:
            raise NotImplementedError(self._event)
        return buffer

    @staticmethod
    def parse(buffer):
        assert isinstance(buffer, bytes)
        if len(buffer) < 8:
            return None, buffer
        stream_id = struct.unpack("!I", buffer[:4])[0]
        event = buffer[4:8]
        params = {}
        if event == EnumStreamEvent.OPEN:
            target_addr_size = buffer[8]
            params["addr"] = buffer[9 : 9 + target_addr_size].decode()
            params["port"] = struct.unpack(
                "!H", buffer[9 + target_addr_size : 11 + target_addr_size]
            )[0]
            token_size = buffer[11 + target_addr_size]
            params["token"] = buffer[
                12 + target_addr_size : 12 + target_addr_size + token_size
            ].decode()
        elif event == EnumStreamEvent.OKAY:
            pass
        elif event == EnumStreamEvent.FAIL:
            reason_size = buffer[8]
            params["reason"] = buffer[9 : 9 + reason_size].decode()
        elif event == EnumStreamEvent.WRITE:
            params["data"] = buffer[8:]
        elif event == EnumStreamEvent.CLOSE:
            pass
        elif event in (EnumStreamEvent.PING, EnumStreamEvent.PONG):
            pass
        else:
            raise NotImplementedError(event)
        return StreamPacket(stream_id, event, **params)


class RelayTransport(object):
    def __init__(self, tunnel, local_id, remote_id=None, token=None):
        self._tunnel = tunnel
        self._local_id = local_id
        self._remote_id = remote_id
        self._token = token
        self._streams = {}
        self._last_stream_id = 0
        self._read_event = asyncio.Event()

    @property
    def streams(self):
        return list(self._streams.values())

    def start_transport(self):
        asyncio.ensure_future(self.process_packet_task())

    def closed(self):
        return self._tunnel.closed()

    async def create_stream(self, address):
        self._last_stream_id += 1
        stream = RelayStream(self, self._last_stream_id, token=self._token)
        if self._remote_id not in self._streams:
            self._streams[self._remote_id] = {}
        self._streams[self._remote_id][self._last_stream_id] = stream
        if await stream.connect(address):
            return stream
        self._streams[self._remote_id][self._last_stream_id] = None
        return None

    async def send_packet(self, stream_packet, remote_id=None):
        remote_id = remote_id or self._remote_id
        assert remote_id is not None
        relay_packet = RelayPacket(self._local_id, remote_id, stream_packet.serialize())
        await self._tunnel.write(relay_packet.serialize())

    async def read_from_tunnel(self):
        return await self._tunnel.read()

    async def process_packet_task(self):
        buffer = b""
        while True:
            try:
                buffer += await self.read_from_tunnel()
            except turbo_tunnel.utils.TunnelClosedError:
                assert self.closed()
                turbo_tunnel.utils.logger.warn(
                    "[%s] Relay tunnel %s closed"
                    % (self.__class__.__name__, self._tunnel)
                )
                break

            while buffer:
                prev_buffer = buffer
                relay_packet, buffer = RelayPacket.parse(buffer)
                if not relay_packet:
                    break

                assert self._remote_id is None or relay_packet.sender == self._remote_id
                assert relay_packet.receiver == self._local_id
                stream_packet = StreamPacket.parse(relay_packet.body)
                stream_id = stream_packet.stream_id
                if relay_packet.sender not in self._streams:
                    self._streams[relay_packet.sender] = {}
                if stream_id not in self._streams[relay_packet.sender]:
                    self._streams[relay_packet.sender][stream_id] = RelayStream(
                        self,
                        stream_id,
                        remote_id=relay_packet.sender,
                        token=self._token,
                    )
                    turbo_tunnel.utils.logger.info(
                        "[%s] New stream %s@%s received"
                        % (self.__class__.__name__, relay_packet.sender, stream_id)
                    )
                if not await self._streams[relay_packet.sender][
                    stream_id
                ].on_recv_packet(stream_packet):
                    turbo_tunnel.utils.logger.warn(
                        "[%s][%s][%s] Handle event %s failed, retry later"
                        % (
                            self.__class__.__name__,
                            relay_packet.sender,
                            stream_packet.stream_id,
                            stream_packet.event.decode(),
                        )
                    )
                    await asyncio.sleep(0.001)
                    buffer = prev_buffer
                else:
                    self._read_event.set()

    def close(self):
        for client_id in self._streams:
            for stream_id in self._streams[client_id]:
                self._streams[client_id][stream_id].close()
        self._streams = {}


class RelayStream(object):
    """Relay Stream"""

    KEEPALIVE_TIMEOUT = 10
    KEEPALIVE_INTERVAL = 60
    KEEPALIVE_RETRY_COUNT = 3

    def __init__(
        self,
        transport,
        stream_id,
        status=EnumStreamStatus.UNINITIALIZED,
        remote_id=None,
        token=None,
    ):
        self._transport = transport
        self._stream_id = stream_id
        self._status = status
        self._remote_id = remote_id
        self._token = token or ""
        self._read_event = asyncio.Event()
        self._buffer = b""
        self._target_tunnel = None
        self._last_recv_time = None

    @property
    def readable(self):
        return self._read_event.is_set()

    @property
    def target_tunnel(self):
        return self._target_tunnel

    async def on_recv_packet(self, packet):
        event = packet.event
        self._last_recv_time = time.time()
        if event == EnumStreamEvent.OPEN:
            assert self._status == EnumStreamStatus.UNINITIALIZED
            assert self._remote_id is not None

            token = packet.token
            if self._token == token:
                target_address = (packet.addr, packet.port)
                turbo_tunnel.utils.logger.info(
                    "[%s] Connect %s:%d"
                    % (self.__class__.__name__, packet.addr, packet.port)
                )
                source_address = (self._remote_id, self._stream_id)
                tunn_conn, self._target_tunnel = await self.connect_server(
                    target_address, source_address
                )
                if self._target_tunnel:
                    self._status = EnumStreamStatus.ESTABLISHED
                    stream_packet = StreamPacket(self._stream_id, EnumStreamEvent.OKAY)
                    turbo_tunnel.utils.AsyncTaskManager().start_task(
                        self.forward(tunn_conn)
                    )
                    turbo_tunnel.utils.AsyncTaskManager().start_task(
                        self.keepalive_task()
                    )
                else:
                    reason = "Connect %s:%d failed" % target_address
                    turbo_tunnel.utils.logger.warn(
                        "[%s] %s" % (self.__class__.__name__, reason)
                    )
                    self._status = EnumStreamStatus.CLOSING
                    stream_packet = StreamPacket(
                        self._stream_id,
                        EnumStreamEvent.FAIL,
                        reason=reason,
                    )
            else:
                turbo_tunnel.utils.logger.warn(
                    "[%s] Client %s access denied"
                    % (self.__class__.__name__, self._remote_id)
                )
                self._status = EnumStreamStatus.CLOSING
                stream_packet = StreamPacket(
                    self._stream_id, EnumStreamEvent.FAIL, reason="Access denied"
                )
            await self._transport.send_packet(stream_packet, self._remote_id)
        elif event == EnumStreamEvent.OKAY:
            if self._status == EnumStreamStatus.OPENING:
                self._status = EnumStreamStatus.ESTABLISHED
                self._read_event.set()
                turbo_tunnel.utils.AsyncTaskManager().start_task(self.keepalive_task())
        elif event == EnumStreamEvent.FAIL:
            if self._status == EnumStreamStatus.OPENING:
                self._status = EnumStreamStatus.CLOSED
                self._read_event.set()
        elif event == EnumStreamEvent.WRITE:
            self._buffer += packet.data
            self._read_event.set()
        elif event == EnumStreamEvent.PING:
            await self.pong()
        elif event == EnumStreamEvent.PONG:
            turbo_tunnel.utils.logger.info(
                "[%s] Received PING from %s@%d"
                % (self.__class__.__name__, self._remote_id, self._stream_id)
            )
            if self._status == EnumStreamStatus.PINGING:
                self._status = EnumStreamStatus.ESTABLISHED
        elif event == EnumStreamEvent.CLOSE:
            if self._buffer:
                return False
            self._status = EnumStreamStatus.CLOSED
            if self._target_tunnel:
                turbo_tunnel.utils.logger.info(
                    "[%s] Close tunnel %s"
                    % (self.__class__.__name__, self._target_tunnel)
                )
                self._target_tunnel.close()
                self._target_tunnel = None
            self._read_event.set()
        else:
            raise NotImplementedError(event)
        return True

    async def connect(self, address):
        stream_packet = StreamPacket(
            self._stream_id,
            EnumStreamEvent.OPEN,
            addr=address[0],
            port=address[1],
            token=self._token,
        )
        await self._transport.send_packet(stream_packet)
        self._status = EnumStreamStatus.OPENING
        await self._read_event.wait()
        self._read_event.clear()
        if self._status == EnumStreamStatus.ESTABLISHED:
            return True
        elif self._status == EnumStreamStatus.CLOSED:
            return False
        else:
            raise StreamStatusError(self._status)

    async def read(self):
        await self._read_event.wait()
        self._read_event.clear()
        if not self._buffer:
            raise StreamClosedError()
        buffer, self._buffer = self._buffer, b""
        return buffer

    async def write(self, buffer):
        stream_packet = StreamPacket(
            self._stream_id, EnumStreamEvent.WRITE, data=buffer
        )
        await self._transport.send_packet(stream_packet, self._remote_id)

    def close(self):
        stream_packet = StreamPacket(self._stream_id, EnumStreamEvent.CLOSE)
        asyncio.ensure_future(
            self._transport.send_packet(stream_packet, self._remote_id)
        )
        self._status = EnumStreamStatus.CLOSED
        turbo_tunnel.utils.logger.info(
            "[%s] Stream %s@%d closed"
            % (self.__class__.__name__, self._remote_id, self._stream_id)
        )

    async def connect_server(self, target_address, source_address):
        """Connect target server"""
        tun_conn = turbo_tunnel.server.TunnelConnection(
            source_address, target_address, None
        )
        tunnel_chain = turbo_tunnel.chain.TunnelChain(
            [turbo_tunnel.utils.Url("tcp://")]
        )
        try:
            await tunnel_chain.create_tunnel(target_address)
        except turbo_tunnel.utils.TunnelError as e:
            turbo_tunnel.utils.logger.warn(
                "[%s] Connect %s:%d failed: %s"
                % (
                    self.__class__.__name__,
                    target_address[0],
                    target_address[1],
                    e,
                )
            )
            tun_conn.on_downstream_closed()
            return tun_conn, None
        else:
            return tun_conn, tunnel_chain.tail

    async def ping(self):
        turbo_tunnel.utils.logger.info(
            "[%s] Ping %s@%d"
            % (self.__class__.__name__, self._remote_id, self._stream_id)
        )
        stream_packet = StreamPacket(self._stream_id, EnumStreamEvent.PING)
        await self._transport.send_packet(stream_packet, self._remote_id)
        self._status = EnumStreamStatus.PINGING

    async def pong(self):
        stream_packet = StreamPacket(self._stream_id, EnumStreamEvent.PONG)
        await self._transport.send_packet(stream_packet, self._remote_id)

    async def keepalive_task(self):
        """keepalive"""
        while self._status != EnumStreamStatus.CLOSED:
            if time.time() - self._last_recv_time < self.__class__.KEEPALIVE_INTERVAL:
                await asyncio.sleep(1)
                continue
            for _ in range(self.__class__.KEEPALIVE_RETRY_COUNT):
                await self.ping()
                time0 = time.time()
                while time.time() - time0 < self.__class__.KEEPALIVE_TIMEOUT:
                    if self._status != EnumStreamStatus.PINGING:
                        break
                    await asyncio.sleep(1)
                else:
                    turbo_tunnel.utils.logger.warn(
                        "[%s] PING timeout, retry later..." % self.__class__.__name__
                    )
                    continue
                break

    async def forward(self, tunn_conn):
        if not self._target_tunnel:
            raise RuntimeError("Target server is not connected")

        async def _forward_to_upstream():
            while True:
                try:
                    buffer = await self.read()
                except StreamClosedError:
                    tunn_conn.on_downstream_closed()
                    if self._target_tunnel:
                        self._target_tunnel.close()
                    break
                tunn_conn.on_data_sent(buffer)
                await self._target_tunnel.write(buffer)

        async def _forward_to_downstream():
            assert self._remote_id is not None
            while True:
                try:
                    buffer = await self._target_tunnel.read()
                except turbo_tunnel.utils.TunnelClosedError:
                    tunn_conn.on_upstream_closed()
                    self.close()
                    break
                tunn_conn.on_data_recevied(buffer)
                await self.write(buffer)

        tasks = [
            turbo_tunnel.utils.AsyncTaskManager().wrap_task(_forward_to_upstream()),
            turbo_tunnel.utils.AsyncTaskManager().wrap_task(_forward_to_downstream()),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        tunn_conn.on_close()


class RelayTunnel(turbo_tunnel.tunnel.Tunnel):
    """Relay Tunnel"""

    outer_tunnel_class = None
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
        tunnel = self.outer_tunnel_class(tunnel, url, address)
        super(RelayTunnel, self).__init__(tunnel, url, address)
        if str(url) in self.__class__.transports:
            self._relay_transport = self.__class__.transports[str(url)]
            self._connected = True
        else:
            self._relay_transport = RelayTransport(
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
                    "[%s] Connection of %s closed, remove cache" % (cls.__name__, key)
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
        except StreamClosedError:
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


def create_random_string(length=32, exclude=None):
    result = ""
    while len(result) < length:
        c = chr(random.randint(48, 122))
        if exclude and c in exclude:
            continue
        result += c
    return result


def win32_daemon():
    cmdline = []
    for it in sys.argv:
        if it not in ("-d", "--daemon"):
            cmdline.append(it)

    DETACHED_PROCESS = 8
    subprocess.Popen(cmdline, creationflags=DETACHED_PROCESS, close_fds=True)
