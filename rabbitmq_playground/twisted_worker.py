from twisted.internet import reactor, defer, protocol
import pika
from pika.adapters.twisted_connection import TwistedProtocolConnection, TwistedChannel
from pika.exchange_type import ExchangeType
from twisted.internet.endpoints import TCP4ClientEndpoint

from twisted.internet.protocol import ClientFactory


# copy-pasta from libdohop
def deferred_sleep(t):
    from twisted.internet import reactor

    later = []

    def _cancel_sleep(later):
        def _(s: 'defer.Deferred[None]') -> None:
            later[0].cancel()

        return _

    d: 'defer.Deferred[None]' = defer.Deferred(canceller=_cancel_sleep(later))
    later.append(reactor.callLater(t, d.callback, None))
    return d


def decorate_coroutine_as_deferred(func):
    def ensure_deferred_wrapper(*args, **kwargs):
        return defer.ensureDeferred(func(*args, **kwargs))

    return ensure_deferred_wrapper


class PerConnectionFactory(ClientFactory):
    def buildProtocol(self, addr):
        parameters = pika.ConnectionParameters()
        protocol = M(parameters)
        return protocol


class M(TwistedProtocolConnection):
    def connectionReady(self):
        reactor.callLater(0, workers_loop, self)


@decorate_coroutine_as_deferred
async def workers_loop(connection: TwistedProtocolConnection):
    try:
        channel: TwistedChannel = await connection.channel()
        await channel.exchange_declare(exchange='msg', exchange_type='fanout')
        res = await channel.queue_declare('hello_q')
        await channel.queue_bind(exchange='msg', queue=res.method.queue)
        res = await channel.queue_declare('hello_q2')
        await channel.queue_bind(exchange='msg', queue=res.method.queue)
        d = channel.basic_consume(queue='hello_q', auto_ack=True)
        d.addCallback(queue_worker, channel)
        d = channel.basic_consume(queue='hello_q2', auto_ack=True)
        d.addCallback(queue_worker_2, channel)
        

        while connection.connected and not connection.is_closed:
            print('workers loop')
            await deferred_sleep(1)

        print('Connection dropped')
    finally:
        await connection.close()


@decorate_coroutine_as_deferred
async def queue_worker(consume, channel):
    queue_object, consumer_tag = consume
    while True:
        r = await queue_object.get()
        # print(r.body)


@decorate_coroutine_as_deferred
async def queue_worker_2(consume, channel):
    print('I do nothing')
    queue_object, consumer_tag = consume
    while True:
        r = await queue_object.get()
        # print('I do nothing')


if __name__ == '__main__':
    # endpoint = TCP4ClientEndpoint(reactor, host='localhost', port=5672)
    # endpoint.listen(PerConnectionFactory())
    reactor.connectTCP('localhost', 5672, PerConnectionFactory())
    reactor.run()