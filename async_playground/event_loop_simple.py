# https://tenthousandmeters.com/blog/python-behind-the-scenes-12-how-asyncawait-works-in-python/
from collections import deque

class EventLoopNoIO:
    def __init__(self):
        self.tasks_to_run = deque([])

    def create_task(self, coro):
        self.tasks_to_run.append(coro)

    def run(self):
        while self.tasks_to_run:
            task = self.tasks_to_run.popleft()
            try:
                next(task)
            except StopIteration:
                continue
            self.create_task(task)



loop = EventLoopNoIO()

import socket

def run_server(host='127.0.0.1', port=55555):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen()
    while True:
        yield
        client_sock, addr = sock.accept()
        print('Connection from', addr)
        loop.create_task(handle_client(client_sock))


def handle_client(sock):
    while True:
        yield
        received_data = sock.recv(4096)
        if not received_data:
            break
        yield
        sock.sendall(received_data)

    print('Client disconnected:', sock.getpeername())
    sock.close()

if __name__ == '__main__':
    loop.create_task(run_server())
    loop.run()