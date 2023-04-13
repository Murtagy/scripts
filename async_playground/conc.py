import threading
import time


x = 1
def increase_x(): global x; x+= 1


def lock_it(): l = threading.Lock(); l.acquire(); time.sleep(5)

threading.active_count()

t = threading.Thread(target=increase_x)
t.name

x= 0
trds = [threading.Thread(target=increase_x) for _ in range(100000)]
for t in trds: t.start()

t1 = threading.Thread(target=lock_it)
t2 = threading.Thread(target=lock_it)
t1.start()
t2.start()

lock = threading.Lock();
def lock_it2(lock):
    print(f'Locking {threading.current_thread().name}')
    with lock:
        print(f'Locked {threading.current_thread().name}')
        time.sleep(5)
    print(f'Released {threading.current_thread().name}')

t1 = threading.Thread(target=lock_it2, args=[lock])
t2 = threading.Thread(target=lock_it2, args=[lock])
t1.start()
t2.start()


from multiprocessing import Process


t1 = Process(target=lock_it)
t2 = Process(target=lock_it)
t1.start()
t2.start()