#!/usr/bin/env python
import pika
from pika.channel import Channel
from pika.spec import Basic

# connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
# connection.close()
with pika.BlockingConnection(pika.ConnectionParameters("localhost")) as connection:
    channel = connection.channel()
    channel.queue_declare("hello_q")
    channel.basic_publish(exchange="", routing_key="hello_q", body="Hello World!")

    def receive_msg(
        channel: Channel,
        method: Basic.Deliver,
        properties: pika.BasicProperties,
        body: bytes,
    ):
        print("Received", body)
        # 1/0

    channel.basic_consume(
        queue="hello_q", auto_ack=True, on_message_callback=receive_msg
    )
    channel.start_consuming()


# NOTES

# ttl of connection:
# 2023-04-13 08:39:18.313651+00:00 [error] <0.919.0> closing AMQP connection <0.919.0> (172.17.0.1:45744 -> 172.17.0.2:5672):
# 2023-04-13 08:39:18.313651+00:00 [error] <0.919.0> missed heartbeats from client, timeout: 60s

# before we run `start_consuming` and after `channel.basic_consume` the messages seem already delivered on dashboard
