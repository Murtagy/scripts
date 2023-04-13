import asyncio
from asyncio import StreamReader, StreamWriter

import urllib
import re

from contextlib import closing
from http.server import BaseHTTPRequestHandler as RequestHandler

from io import BytesIO, StringIO
from typing import Tuple

import asyncio_redis


StreamPair = Tuple[StreamReader, StreamWriter]

class HTTPRequest(RequestHandler):
    def __init__(self, request_text: bytes):
        self.rfile = BytesIO(request_text)
        self.raw_requestline = self.rfile.readline()
        self.error_code = self.error_message = None
        self.parse_request()

    def send_error(self, code, message):
        self.error_code = code
        self.error_message = message

HOST = '127.0.0.1'
PORT = 8888

# REGEX_PARSER =  re.compile(br'(?P<method>[a-zA-Z]+) (?P<uri>(\w+://)?(?P<host>[^\s\'\"<>\[\]{}|/:]+)(:(?P<port>\d+))?[^\s\'\"<>\[\]{}|]*)')
REGEX_CONTENT_LENGTH_STR = re.compile(br'(\d{1,10})')

async def client_connected_cb(reader: StreamReader, writer: StreamWriter):
    with closing(writer):
        #print('Got connection')

        # READ
        data = await reader.read(10240)
        # print(data.decode())

        # PARSE
        parsed = parse(data)
        path = parsed.path
        method = parsed.command

        if method == 'GET':
            cached = await REDIS.get(parsed.path)
            #print(f'Cached {cached} {type(cached)} {cached == b""}')
            if cached is None or cached == '':
                #print(path, 'No cache hit!')
                pass
            else:
                cached = bytes(cached, encoding='utf8')
                #print('CACHE HIT', cached)
                writer.write(cached)
                await writer.drain()
                return

        # forward
        host = 'httpbin.org'
        port = 80  # 443 - https port

        remote_reader, remote_writer = await asyncio.open_connection(host, port)
        with closing(remote_writer):
            #print('Openned connection to remote')
            remote_writer.write(data)
            await remote_writer.drain()

            # now get response
            data_to_cache = BytesIO()
            await forward_until_timeout(remote_reader, writer, path, data_to_cache)
            writer.close()
            await writer.wait_closed()
            data_to_cache.seek(0)
            v = data_to_cache.read().decode()
            #print('Setting key', path, v)
            await REDIS.set(path, v)


async def forward_until_timeout(reader, writer, path, data_to_cache):
    #print('In forward')
    finished = False

    data_len = 0
    body_len = 0
    headers_finished = False
    found_len = False
    len_ = 0
    while True:
        try:
            #print('.', end='')
            if not headers_finished:
                data = await asyncio.wait_for(reader.readline(), 1)
                line = data.decode()
                #print(repr(line))

                if data == b'\r\n' or data == '\b':
                    #print('Finished reading headers')
                    headers_finished = True

                if 'Content-Length' in line:
                    m = REGEX_CONTENT_LENGTH_STR.search(data)
                    len_ = int(m[0])
                    #print('CONTENTLENGTH: ', len_)
                    found_len = True
            else:
                data = await asyncio.wait_for(reader.read(100), 1)

            if headers_finished:
                if data != b'\r\n' and data != '\b':
                    body_len += len(data)
                    #print('body', body_len)
            #print('data', len(data), body_len, len_)

        except asyncio.TimeoutError:
            continue

       #print('Writing...', end='')
        writer.write(data)
        await writer.drain()
        #print('OK', )

        data_to_cache.write(data)

        if data == b'' or (found_len and body_len == len_):  # when closed
            #print('Data finished')
            break

class HTTPS(ValueError):
    ...

def parse(data):
    parsed = HTTPRequest(data)
    # #print(dir(parsed))

    if parsed:
        method = parsed.command
        request_version = parsed.request_version
        # #print(method, request_version)
        # if method == 'OPTIONS' and request_version.startswith('HTTPS'):
        #     return parsed

        path = parsed.path
        host = parsed.headers['Host']
        port = None
        if ':' in host:
            host, port = host.split(':')
        # #print(method, path, host, port)

    return parsed


REDIS = None

async def main():
    global REDIS
    REDIS = await asyncio_redis.Pool.create(host='localhost', port=6379, poolsize=10)
    server = await asyncio.start_server(client_connected_cb, host=HOST, port=PORT)

    async with server:
        print('Test redis')
        await REDIS.set('12', '3')
        print(await REDIS.get('12'), '= 3')
        print(f'Serving at {HOST}:{PORT}')
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
