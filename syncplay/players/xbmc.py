import random

from syncplay import constants
from syncplay.players.basePlayer import BasePlayer

from twisted.internet.defer import inlineCallbacks


class NoActivePlayer(Exception):
    pass


class XbmcPlayer(BasePlayer):
    # Kind of is, but only selected integral speeds.
    speedSupported = False
    # Maybe later..
    customOpenDialog = False
    secondaryOSDSupported = False
    osdMessageSeparator = '; '

    def __init__(self, client, url, filePath=None):
        from twisted.internet import reactor
        self.reactor = reactor
        self.client = client
        self.proxy = Proxy(url.encode('ascii', 'replace') + '/jsonrpc')
        self._ping_pending = False

        # Poll the active playing file occasionally to update the client.
        from twisted.internet.task import LoopingCall
        self._file_props = (None, None, None)
        self.file_poll = LoopingCall(self.updatePlayingFile)
        self.file_poll.start(1.5)

        if filePath:
            self.openFile(filePath)
        self.reactor.callFromThread(self.client.initPlayer, self)

    @staticmethod
    def run(client, playerPath, filePath, args):
        """Get an instance of the Player."""
        return XbmcPlayer(client, playerPath, filePath)

    def callRemote(self, method, *args, **kwargs):
        return self.proxy.callRemote(method, *args, **kwargs)

    def _activePlayer(self):
        d = self.callRemote('Player.GetActivePlayers')
        def findVideoPlayerId(players):
            for p in players:
                if p['type'] == 'video':
                    return p['playerid']
        d.addCallback(findVideoPlayerId)
        return d

    @inlineCallbacks
    def askForStatus(self, cookie=None):
        """
        Get player pause state and position, passing back to
        client.updatePlayerStatus.
        """
        # Client tends to call this faster than we can get RPC responses. Only
        # queue a new request if there isn't one pending.
        if self._ping_pending:
            return
        self._ping_pending = True

        pid = yield self._activePlayer()
        if pid:
            resp = yield self.callRemote('Player.GetProperties', playerid=pid,
                                         properties=['speed', 'time'])
            paused = resp['speed'] == 0
            t = resp['time']
            position = 3600 * t['hours'] + 60 * t['minutes'] + \
                       t['seconds'] + t['milliseconds'] / 1000
        else:
            paused = True
            position = 0
        self.client.updatePlayerStatus(paused, position, cookie=cookie)
        self._ping_pending = False

    @inlineCallbacks
    def updatePlayingFile(self):
        """Called periodically to check what file is playing."""
        pid = yield self._activePlayer()
        if not pid:
            name = length = path = None
        else:
            props = yield self.callRemote('Player.GetItem', playerid=pid,
                                          properties=['file', 'runtime'])
            item = props['item']
            name = item['label']
            length = item['runtime']   # in seconds
            path = item['file']
        # client.updateFile pushes data out to the network, assuming it
        # changed. Only do so if there actually was a change.
        if (name, length, path) != self._file_props:
            self._file_props = (name, length, path)
            self.client.updateFile(name, length, path)

    def displayMessage(self, message, duration=constants.OSD_DURATION * 1000,
                       secondaryOSD=False):
        self.callRemote('GUI.ShowNotification', title='SyncPlay',
                        message=message, image='info')

    def drop(self):
        self.file_poll.stop()

    @inlineCallbacks
    def setPaused(self, value):
        pid = yield self._activePlayer()
        if pid:
            self.callRemote('Player.PlayPause', playerid=pid,
                            play=not value)

    @inlineCallbacks
    def setPosition(self, value):
        hours, value = divmod(value, 3600)
        minutes, value = divmod(value, 60)
        seconds, value = divmod(value, 1)
        milliseconds = value * 1000
        timespec = {
            'hours': int(hours),
            'minutes': int(minutes),
            'seconds': int(seconds),
            'milliseconds': int(milliseconds)
        }

        pid = yield self._activePlayer()
        if pid:
            self.callRemote('Player.Seek', playerid=pid,
                            value=timespec)

    def setSpeed(self, value):
        # Not supported
        pass

    def openFile(self, filePath, resetPosition=False):
        # TODO should display some warning or whatnot; opening files is flaky for
        # remote hosts.
        self.callRemote('Player.Open', item={'file': filePath})
        # File change notification is handled by polling

    @staticmethod
    def isValidPlayerPath(path):
        return True
        # TODO must be *fast*
        try:
            conn = jsonrpclib.Server(path + '/jsonrpc')
            props = getattr(conn, 'Application.GetProperties')(properties=['name'])
            if props['name'] in ('XBMC', 'Kodi'):
                version = getattr(conn, 'JSONRPC.Version')()['version']
                if version['major'] >= 6:
                    return True
        except:
            pass
        return False

    @staticmethod
    def getPlayerPathErrors(playerPath, filePath):
        # TODO should be a url, filePath is not supported? Other stuff.
        return None

    @staticmethod
    def getDefaultPlayerPathsList():
        return ['http://localhost:8080']

    @classmethod
    def getIconPath(cls, path):
        if cls.isValidPlayerPath(path):
            return constants.XBMC_ICONPATH
    
    @staticmethod
    def getExpandedPath(path):
        return path

# === jsonrpc largely borrowed from txjsonrpc ===
import json
import urlparse
import twisted.internet.protocol
import twisted.internet.defer
import twisted.web.http

class Proxy(object):
    def __init__(self, url):
        scheme, netloc, path, params, query, fragment = urlparse.urlparse(url)
        # username/password in URL
        netloc_parts = netloc.split('@')
        if len(netloc_parts) == 2:
            userpass = netloc_parts.pop(0).split(':')
            self.user = userpass.pop(0)
            try:
                self.password = userpass.pop(0)
            except IndexError:
                self.password = None
        else:
            self.user = self.password = None
        # Hostname, port number, path
        hostport = netloc_parts[0].split(':')
        self.host = hostport.pop(0)
        try:
            self.port = int(hostport.pop(0))
        except IndexError:
            self.port = None
        self.path = path or '/'

    def callRemote(self, method, *args, **kwargs):
        # TODO should open one connection and keep it open. One factory
        # instance also allows IDs to be assigned sensibly.
        factory = QueryFactory(self.path, self.host, self.user, self.password,
                               method, args, kwargs)
        twisted.internet.reactor.connectTCP(self.host, self.port or 80, factory)
        return factory.deferred


class QueryProtocol(twisted.web.http.HTTPClient):
    def connectionMade(self):
        self.sendCommand('POST', self.factory.path)
        self.sendHeader('Content-Type', 'application/json')
        self.sendHeader('Content-Length', str(len(self.factory.payload)))
        if self.factory.user:
            auth = '{}:{}'.format(self.factory.user, self.factory.password)
            auth = auth.encode('base64').strip()
            self.sendHeader('Authorization', 'Basic {}'.format(auth))
        self.endHeaders()
        self.transport.write(self.factory.payload)

    def handleStatus(self, version, status, message):
        if status != '200':
            self.factory.badStatus(status, message)

    def handleResponse(self, contents):
        self.factory.parseResponse(contents)


class QueryFactory(twisted.internet.protocol.ClientFactory):
    protocol = QueryProtocol

    def __init__(self, path, host, user, password, method, args, kwargs):
        self.path, self.host = path, host
        self.user, self.password = user, password
        self.deferred = twisted.internet.defer.Deferred()
        # Build request payload
        self.payload = json.dumps({
            'jsonrpc': '2.0',
            'id': '1',          # XXX
            'method': method,
            'params': kwargs
        })

    @staticmethod
    def loads(s):
        out = json.loads(s)
        assert isinstance(out, dict)
        assert ('result' in out or 'error' in out) and 'id' in out
        if out.get('error') is not None:
            raise Fault(out['error']['code'], out['error']['data'])
        else:
            return out['result']

    def parseResponse(self, contents):
        if self.deferred is None:
            return
        try:
            result = self.loads(contents)
        except Exception, error:
            self.deferred.errback(error)
            self.deferred = None
        else:
            self.deferred.callback(result)
            self.deferred = None

    def clientConnectionFailed(self, _, reason):
        if self.deferred is not None:
            self.deferred.errback(reason)
            self.deferred = None

    clientConnectionLost = clientConnectionFailed

    def badStatus(self, status, message):
        self.deferred.errback(ValueError(status, message))
        self.deferred = None

