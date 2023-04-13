from collections import deque
import selectors

class EventLoopNoIO:
    def __init__(self):
        self.tasks_to_run = deque([])
        self.sel = selectors.DefaultSelector()

    def create_task(self, coro):
        self.tasks_to_run.append(coro)

    def run(self):
        while True:
            # print('T', len(self.tasks_to_run), bool(self.tasks_to_run))
            if self.tasks_to_run:
                task = self.tasks_to_run.popleft()
                # print('task', task)
                try:
                    op,arg = next(task)
                except StopIteration:
                    continue

                if op == 'wait_read':
                    self.sel.register(arg, selectors.EVENT_READ, task)
                elif op == 'wait_write':
                    self.sel.register(arg, selectors.EVENT_WRITE, task)
                else:
                    raise ValueError('Unknown event lopp operation: ', op)

            else:
                for key, _ in self.sel.select():
                    task = key.data
                    sock = key.fileobj
                    self.sel.unregister(sock)
                    self.create_task(task)



loop = EventLoopNoIO()

import socket

def run_server(host='127.0.0.1', port=55555):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen()
    print('Listening...')
    while True:
        yield 'wait_read', sock
        client_sock, addr = sock.accept()
        print('Connection from', addr)
        loop.create_task(handle_client(client_sock))


def handle_client(sock):
    while True:
        yield 'wait_read', sock
        received_data = sock.recv(4096)
        if not received_data:
            break
        yield 'wait_write', sock
        sock.sendall(received_data)

    print('Client disconnected:', sock.getpeername())
    sock.close()

if __name__ == '__main__':
    s = run_server()
    loop.create_task(s)
    loop.run()