from twisted.internet import reactor, defer


def delayed_data(data, delay):
    deferred = defer.Deferred()
    reactor.callLater(delay, deferred.callback, data)
    return deferred


def print_data(result):
    print(result)

deferred = delayed_data('123', 5)
deferred.addCallback(print_data)

reactor.callLater(10, reactor.stop)

print('Starting...')
reactor.run()