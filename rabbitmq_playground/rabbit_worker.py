#!/usr/bin/env python
import pika
from pika.channel import Channel
from pika.spec import Basic

with pika.BlockingConnection(pika.ConnectionParameters("localhost")) as connection:
    channel = connection.channel()
    channel.queue_declare("hello_q")

    def receive_msg(
        channel: Channel,
        method: Basic.Deliver,
        properties: pika.BasicProperties,
        body: bytes,
    ):
        print("Received", body)

    channel.basic_consume(
        queue="hello_q", auto_ack=True, on_message_callback=receive_msg
    )
    channel.start_consuming()
