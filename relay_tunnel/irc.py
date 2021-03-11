# -*- coding: utf-8 -*-

"""IRC Relay Tunnel
"""

import asyncio
import base64
import contextlib
import struct

import turbo_tunnel

from . import utils


class IRCPacket(object):
    def __init__(self, body):
        assert len(body) < 65536
        self._body = body

    @property
    def body(self):
        return self._body

    @classmethod
    def checksum(cls, data):
        """计算效验值"""
        s = 0
        n = len(data) % 2
        for i in range(0, len(data) - n, 2):
            s += (data[i] << 8) + (data[i + 1])
        if n:
            s += data[i + 1] << 8

        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)

        s = ~s & 0xFFFF
        return s

    def serialize(self):
        buffer = struct.pack(">H", len(self._body))
        buffer += self._body
        if len(self._body) % 2:
            buffer += b"\x00"
        cs = self.checksum(buffer)
        buffer += struct.pack(">H", cs)
        return buffer

    @classmethod
    def parse(cls, buffer):
        if cls.checksum(buffer):
            turbo_tunnel.utils.logger.warn(
                "[%s] Invalid checksum: %r" % (cls.__name__, buffer)
            )
            return None
        buff_size = struct.unpack(">H", buffer[:2])[0]
        buff = buffer[2:-2]
        if len(buff) % 2 or (len(buff) != buff_size and len(buff) != buff_size + 1):
            turbo_tunnel.utils.logger.warn(
                "[%s] Invalid buffer size: [%d] %r" % (cls.__name__, buff_size, buff)
            )
            return None
        if len(buff) == buff_size + 1:
            if buff[-1]:
                turbo_tunnel.utils.logger.warn(
                    "[%s] Invalid buffer padding: %r" % (cls.__name__, buff)
                )
                return None
            buff = buff[:-1]
        return IRCPacket(buff)


class IRCTunnel(turbo_tunnel.tunnel.TCPTunnel):

    timeout = 60
    fragment_size = 320

    def __init__(self, tunnel, url, address=None):
        super(IRCTunnel, self).__init__(tunnel, url, address)
        self._username = self._url.params.get("client_id")
        self._token = self._url.params.get("token")
        self._buffers = {}
        self._read_event = asyncio.Event()
        self._ack_events = {}
        self._message_id = 0

    async def _processing_message_task(self):
        while True:
            message = await self.readline()
            message = message.decode().strip().split(" ", 3)
            if (
                not self._connected
                and len(message) > 3
                and ":Welcome to the" in message[3]
            ):
                self._connected = True
            elif message[0] == "ERROR":
                turbo_tunnel.utils.logger.error(
                    "[%s] %s" % (self.__class__.__name__, " ".join(message[1:]))
                )
                break
            elif message[0] == "PING":
                await self.write(b"PONG %b\r\n" % message[1].encode())
            elif message[1] == "PRIVMSG":
                pass
            elif message[1] == "NOTICE":
                if message[2] == "*":
                    # Ignore broadcast
                    continue
                if message[3][1:].startswith("*"):
                    continue
                sender = message[0].split("!")[0][1:]
                if message[3].startswith(":===ACK==="):
                    message_id = int(message[3][10:].strip())
                    self._ack_events[sender].set()
                    turbo_tunnel.utils.logger.debug(
                        "[%s] Recv ACK of message %d"
                        % (self.__class__.__name__, message_id)
                    )
                    continue

                message_id, message = message[3][1:].split(".", 1)
                message_id = int(message_id)
                await self.write(
                    b"NOTICE %b :===ACK===%d\r\n" % (sender.encode(), message_id)
                )
                if sender not in self._buffers:
                    self._buffers[sender] = {
                        "buffer": b"",
                        "last_message_id": 0,
                        "cache": {},
                    }
                turbo_tunnel.utils.logger.debug(
                    "[%s][%s][%d] Recv %d bytes message"
                    % (self.__class__.__name__, sender, message_id, len(message))
                )

                buffer = self.decode_message(message)
                if buffer:
                    fire_event = False
                    self._buffers[sender]["cache"][message_id] = buffer
                    while self._buffers[sender]["cache"]:
                        if (
                            self._buffers[sender]["last_message_id"] + 1
                        ) in self._buffers[sender]["cache"]:
                            self._buffers[sender]["last_message_id"] += 1
                            self._buffers[sender]["buffer"] += self._buffers[sender][
                                "cache"
                            ].pop(self._buffers[sender]["last_message_id"])
                            fire_event = True
                        else:
                            break
                    if fire_event:
                        self._read_event.set()

    def decode_message(self, message):
        try:
            buffer = base64.b64decode(message)
        except TypeError:
            return b""
        pkt = IRCPacket.parse(buffer)
        if not pkt:
            return b""
        return pkt.body

    async def connect(self):
        if not await super(IRCTunnel, self).connect():
            return False
        turbo_tunnel.utils.AsyncTaskManager().start_task(
            self._processing_message_task()
        )

        buffer = b"NICK %b\r\n" % self._username.encode()
        buffer += b"USER %b 0 * :...\r\n" % self._username.encode()
        await self.write(buffer)

        try:
            await self.wait_for_connecting()
        except turbo_tunnel.utils.TimeoutError:
            return False
        else:
            await asyncio.sleep(1)
            return True

    async def read_packet(self):
        while True:
            for sender in self._buffers:
                buffer = self._buffers[sender]["buffer"]
                if buffer:
                    packet, buffer = utils.RelayPacket.parse(buffer, self._token)
                    self._buffers[sender]["buffer"] = buffer
                    if packet:
                        return packet
            self._read_event.clear()
            await self._read_event.wait()

    async def write_packet(self, packet):
        buffer = packet.serialize()
        offset = 0
        index = 0
        while offset < len(buffer):
            buff = buffer[offset : offset + self.fragment_size]
            buff = IRCPacket(buff).serialize()
            self._message_id += 1
            command = "NOTICE"  # "PRIVMSG"
            buff = b"%b %b :%d.%b\r\n" % (
                command.encode(),
                packet.receiver.encode(),
                self._message_id,
                base64.b64encode(buff),
            )

            if packet.receiver not in self._ack_events:
                self._ack_events[packet.receiver] = asyncio.Event()
            else:
                self._ack_events[packet.receiver].clear()
            timeout = 10
            while not self._ack_events[packet.receiver].is_set():
                await self.write(buff)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._ack_events[packet.receiver].wait(), timeout
                    )
            offset += self.fragment_size
            # await asyncio.sleep(0.5)
            index += 1


class IRCRelayTunnel(utils.RelayTunnel):
    """IRC Relay Tunnel"""

    underlay_tunnel_class = IRCTunnel


turbo_tunnel.registry.tunnel_registry.register("irc+relay", IRCRelayTunnel)
