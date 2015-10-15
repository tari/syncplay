import json
import random
import urllib2
import urlparse

from syncplay import constants
from syncplay.players.basePlayer import BasePlayer


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
        # twisted must not be imported at file-scope, because it mucks
        # up registering the Qt reactor.
        from twisted.internet import reactor
        from twisted.internet.task import LoopingCall
        # Decorator, really
        from twisted.internet.defer import inlineCallbacks
        for method in ('askForStatus', 'updatePlayingFile', 'setPaused', 'setPosition'):
            setattr(self, method, inlineCallbacks(getattr(self, method)))
        # We need JsonRpcAgent to inherit from web.client.Agent, but can't do
        # it in the declaration. Create a wrapper class here.
        import twisted.web.client
        class Proxy(JsonRpcAgent, twisted.web.client.Agent):
            def __init__(self, reactor, url, connectTimeout=None):
                twisted.web.client.Agent.__init__(self, reactor,
                                                  connectTimeout=connectTimeout)
                JsonRpcAgent.__init__(self, url)

        self.reactor = reactor
        self.client = client
        self.proxy = Proxy(reactor, url.encode('ascii', 'replace') + '/jsonrpc')
        self._ping_pending = False

        # Poll the active playing file occasionally to update the client.
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
        if self.file_poll.running:
            self.file_poll.stop()

    def setPaused(self, value):
        pid = yield self._activePlayer()
        if pid:
            self.callRemote('Player.PlayPause', playerid=pid,
                            play=not value)

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
        url, headers = extractURLAuth(path)
        headers['Content-Type'] = 'application/json'
        req = urllib2.Request(url + '/jsonrpc', json.dumps({
            'jsonrpc': '2.0',
            'method': 'Application.GetProperties',
            'params': {
                'properties': ['name', 'version']
            },
            'id': 1
        }), headers)
        try:
            conn = urllib2.urlopen(req, timeout=1)
            if conn.getcode() != 200:
                return False

            response = json.load(conn)['result']
            if response['name'] in ('XBMC', 'Kodi') and \
                    response['version']['major'] >= 12:
                # JSON-RPC API v6
                return True
        except Exception as e:
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


class HttpError(Exception): pass
class RpcError(Exception): pass

def extractURLAuth(url):
    target = urlparse.urlsplit(url)
    header = {}
    if target.username is not None:
        auth = '{}:{}'.format(target.username, target.password)
        auth = auth.encode('base64').strip()
        header['Authorization'] = 'Basic {}'.format(auth)

    scheme, netloc, path, query, fragment = target
    if '@' in netloc:
        netloc = netloc.partition('@')[2]
    return urlparse.urlunsplit((scheme, netloc, path, query, fragment)), header

class JsonRpcProducer(object):
    def __init__(self, id, method, kwargs):
        self.body = json.dumps({
            'jsonrpc': '2.0',
            'id': id,
            'method': method,
            'params': kwargs
        })
        self.length = len(self.body)

    def startProducing(self, consumer):
        from twisted.internet.defer import succeed
        consumer.write(self.body)
        return succeed(None)

    def pauseProducing(self): pass
    def stopProducing(self): pass


class JsonRpcAgent(object):
    def __init__(self, url):
        from twisted.web.http_headers import Headers
        self.id_seqnum = 0
        self.headers = Headers({
            'Content-Type': ['application/json'],
        })

        # Parse the URL to determine what Authorization header to include
        self.url, auth_header = extractURLAuth(url)
        for k, v in auth_header.iteritems():
            self.headers.addRawHeader(k, v)


    def callRemote(self, method, *args, **kwargs):
        self.id_seqnum += 1
        d = self.request('POST', self.url, self.headers,
                         JsonRpcProducer(self.id_seqnum, method, kwargs))
        
        def handleBody(s):
            """
            Receives response body contents and turns them into json, or
            triggers errback.
            """
            out = json.loads(s)
            assert isinstance(out, dict)
            assert ('result' in out or 'error' in out) and 'id' in out
            if out.get('error') is not None:
                raise RpcError(out['error']['code'], out['error']['data'])
            else:
                return out['result']

        def handleResponse(r):
            from twisted.web.client import readBody
            if r.code == 200:
                read_body = readBody(r)
                read_body.addCallback(handleBody)
                return read_body
            else:
                raise HttpError(r.code)

        # request -> handleResponse -> handleBody -> user
        d.addCallback(handleResponse)
        return d
