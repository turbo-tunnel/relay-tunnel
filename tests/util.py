# -*- coding: utf-8 -*-

import random
import socket

import tornado.tcpserver


class DemoTCPServer(tornado.tcpserver.TCPServer):
    def __init__(self, autoclose=False, ssl_options=None):
        super(DemoTCPServer, self).__init__(ssl_options=ssl_options)
        self._autoclose = autoclose
        self._stream = None

    @property
    def stream(self):
        return self._stream

    def kick_client(self):
        if self._stream:
            self._stream.close()
            self._stream = None

    async def handle_stream(self, stream, address):
        if not self._stream:
            self._stream = stream
        buffer = await stream.read_until(b"\n")
        await stream.write(buffer)
        if self._autoclose:
            stream.close()


def get_random_port():
    while True:
        port = random.randint(10000, 65000)
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
        except:
            continue
        else:
            s.close()
            return port
