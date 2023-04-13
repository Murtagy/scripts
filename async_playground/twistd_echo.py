# https://twistedmatrix.com/documents/current/core/howto/servers.html
from twisted.internet.protocol import Factory
from twisted.internet.protocol import Protocol
from twisted.internet.endpoints import TCP4ServerEndpoint
from twisted.internet import reactor

# my
class Echo(Protocol):
    ''' A Twisted protocol handles data in an asynchronous manner. The protocol responds to events as they arrive from the network and the events arrive as calls to methods on the protocol.'''
    def dataReceived(self, data):
        print('pong')
        self.transport.write(data)
        self.transport.loseConnection()

class EchoFactory(Factory):
    '''  the Factory does not listen to connections, and in fact does not know anything about the network '''
    def buildProtocol(self, addr):
        return Echo()

# cp from how-to
class Echo2(Protocol):
    def __init__(self, factory):
        self.factory = factory

    def connectionMade(self):
        self.factory.numProtocols = self.factory.numProtocols + 1
        self.transport.write(
            # example missed bytes convertion
            b"Welcome! There are currently %d open connections.\n" %
            (self.factory.numProtocols,))

    def connectionLost(self, reason):
        self.factory.numProtocols = self.factory.numProtocols - 1

    def dataReceived(self, data):
        self.transport.write(data)

class Echo2Factory(Factory):
    '''  The factory is used to share state that exists beyond the lifetime of any given connection '''
    def buildProtocol(self, addr):
        return Echo2(self)

    numProtocols = 0


endpoint = TCP4ServerEndpoint(reactor, 8007)
endpoint.listen(EchoFactory())

endpoint = TCP4ServerEndpoint(reactor, 8008)
endpoint.listen(Echo2Factory())



reactor.run()
