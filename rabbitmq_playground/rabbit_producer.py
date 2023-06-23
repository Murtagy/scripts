#!/usr/bin/env python
import pika
from pika.channel import Channel
from pika.spec import Basic

with pika.BlockingConnection(pika.ConnectionParameters("localhost")) as connection:
    channel = connection.channel()
    channel.exchange_declare(exchange='msg', exchange_type='fanout')
    # res = channel.queue_declare("hello_q")
    # channel.queue_bind(exchange='msg', queue=res.method.queue)
    for _ in range(100000):
        channel.basic_publish(
            exchange="msg",
            # routing_key="hello_q",
            routing_key="",
            body="Hello World!",
        )
