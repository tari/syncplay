import functools
import jsonrpclib
import os.path
import xmlrpclib

from httplib import HTTPConnection
from Queue import Queue
from threading import Thread, Event

from syncplay import constants
from syncplay.players.basePlayer import BasePlayer


class NoActivePlayer(Exception):
    pass


class RpcController(Thread):
    def __init__(self, url, readyCallback):
        Thread.__init__(self, name='XBMC RPC Interface')
        self.daemon = True

        self.readyCallback = readyCallback
        self.url = url
        self._connection = None
        self._q = Queue()
        self._stop = False
        self._barrier = 0

    def _call(self, method, **kwargs):
        return getattr(self._connection, method)(**kwargs)

    def _activePlayer(self):
        players = self._call('Player.GetActivePlayers')
        for p in players:
            if p['type'] == 'video':
                return p['playerid']

    def _queue_request(self, f, args, barrier, evt):
        """
        Queue an RPC which will call f(*args).

        If barrier is True, output from status checks which complete after this
        request is queued but before it completes will be suppressed. This
        ensures conflicting playback status will not leak out to the network
        and cause "fighting".
        """
        assert self.isAlive()
        if barrier:
            # There may be multiple barrier requests queued at any given time,
            # so we need to track the barrier depth.
            assert self._barrier >= 0
            self._barrier += 1
        self._q.put((f, args, barrier, evt))

    def queue_request(self, f, *args):
        self._queue_request(f, args, False, None)

    def queue_barrier_request(self, f, *args):
        self._queue_request(f, args, True, None)

    def blocking_request(self, f, *args):
        """
        Same as queue_request but blocks until the request completes.
        """
        evt = Event()
        self._queue_request(f, args, True, evt)
        evt.wait()

    def stop(self):
        def request_stop(_):
            self._stop = True
        self.blocking_request(request_stop)

    def run(self):
        self._connection = jsonrpclib.Server(self.url)
        # Stop any active players
        #for player in self._call('Player.GetActivePlayers'):
        #    self._call('Player.Stop', playerid=player['playerid'])
        # Fire "ready" callback
        self.readyCallback(self)

        # Process RPC requests until asked to stop
        while not self._stop:
            (f, args, is_barrier, evt) = self._q.get()
            # Barriers are always executed; stop suppression
            if is_barrier:
                self._barrier -= 1
                f(self, *args)
            else:
                # Skip if a barrier is preventing processing, but requeue. The
                # caller allows only one status request in flight at a time and
                # losing that one would be bad.
                if self._barrier > 0:
                    # Requeue, but deadlock would be catastrophic. Do not mark
                    # as complete.
                    self._q.put_nowait((f, args, is_barrier, evt))
                    continue
                else:
                    f(self, *args)
            # Mark done
            if evt is not None:
                evt.set()


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

        self._ping_pending = False
        self.rpc = RpcController(url + '/jsonrpc', self._rpcReady)
        self.rpc.start()
        # Poll the active playing file occasionally to update the client
        # Late-bind to twisted to sidestep wonky issues with whatever the main
        # program does to twisted.
        from twisted.internet.defer import Deferred
        from twisted.internet.task import LoopingCall
        self._Deferred = Deferred
        self._file_props = (None, None, None)
        self.file_poll = LoopingCall(self._ping_file)
        self.file_poll.start(1.5)

        if filePath:
            self.openFile(filePath)

    def _rpcReady(self, rpc):
        # RPC handler is ready, so the player is up
        self.reactor.callFromThread(self.client.initPlayer, self)

    @staticmethod
    def run(client, playerPath, filePath, args):
        """Get an instance of the Player."""
        return XbmcPlayer(client, playerPath, filePath)

    def askForStatus(self):
        """
        Get player pause state and position, passing back to
        client.updatePlayerStatus.
        """
        # Client tends to call this faster than we can get RPC responses. Only
        # queue a new request if there isn't one pending.
        if not self._ping_pending:
            self.rpc.queue_request(self._ping_playback)
            self._ping_pending = True

    def _ping_playback(self, rpc):
        pid = rpc._activePlayer()
        if pid:
            resp = rpc._call('Player.GetProperties', playerid=pid, properties=['speed', 'time'])
            paused = resp['speed'] == 0
            t = resp['time']
            position = 3600 * t['hours'] + 60 * t['minutes'] + \
                       t['seconds'] + t['milliseconds'] / 1000
        else:
            paused = True
            position = 0
        self._ping_pending = False
        self.reactor.callFromThread(self.client.updatePlayerStatus, paused, position)

    def _ping_file(self):
        d = self._Deferred()
        self.rpc.queue_request(self._rpc_ping_file, d)
        return d

    def _rpc_ping_file(self, rpc, d):
        pid = rpc._activePlayer()
        if pid:
            props = rpc._call('Player.GetItem', playerid=pid, properties=['file', 'runtime'])['item']
            name = props['label']
            length = props['runtime']   # in seconds
            path = props['file']
        else:
            name = length = path = None
        # Client notifies on every call here, so don't call back unless the file
        # actually changed.
        if (name, length, path) != self._file_props:
            self._file_props = (name, length, path)
            self.reactor.callFromThread(self.client.updateFile, name, length, path)
        # Little silly, but need to do the callback in the reactor thread
        self.reactor.callFromThread(d.callback, None)

    def displayMessage(self, message, duration=constants.OSD_DURATION * 1000,
                       secondaryOSD=False):
        self.rpc.queue_request(self._displayMessage, message, duration)

    def _displayMessage(self, rpc, message, duration):
        rpc._call('GUI.ShowNotification', title='SyncPlay Client', message=message, image='info')

    def drop(self):
        self.file_poll.stop()
        self.rpc.stop()

    def setPaused(self, value):
        self.rpc.queue_barrier_request(self._setPaused, value)

    def _setPaused(self, rpc, value):
        pid = rpc._activePlayer()
        if pid:
            rpc._call('Player.PlayPause', playerid=pid, play=not value)

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
        self.rpc.queue_barrier_request(self._setPosition, timespec)

    def _setPosition(self, rpc, timespec):
        pid = rpc._activePlayer()
        if pid:
            rpc._call('Player.Seek', playerid=pid, value=timespec)

    def setSpeed(self, value):
        # Not supported
        pass

    def openFile(self, filePath, resetPosition=False):
        # TODO should display some warning or whatnot; opening files is flaky for
        # remote hosts.
        self.rpc.queue_barrier_request(self._openFile, filePath, os.path.basename(filePath))

    def _openFile(self, rpc, remotePath, name):
        rpc._call('Player.Open', item={'file': remotePath})
        # File change notification is handled by polling

    @staticmethod
    def isValidPlayerPath(path):
        # TODO must be *fast*
        try:
            conn = jsonrpclib.Server(path + '/jsonrpc')
            props = getattr(conn, 'Application.GetProperties')(properties=['name'])
            if props['name'] in ('XBMC', 'Kodi'):
                version = getattr(conn, 'JSONRPC.Version')()['version']
                if version['major'] >= 6:
                    return True
        except (IOError, xmlrpclib.Error) as e:
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
