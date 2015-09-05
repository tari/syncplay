from syncplay.players.mplayer import MplayerPlayer
from syncplay.players.mpv import MpvPlayer
from syncplay.players.vlc import VlcPlayer
from syncplay.players.xbmc import XbmcPlayer
try:
    from syncplay.players.mpc import MPCHCAPIPlayer
except ImportError:
    from syncplay.players.basePlayer import DummyPlayer 
    MPCHCAPIPlayer = DummyPlayer
    
def getAvailablePlayers():
    return [MPCHCAPIPlayer, MplayerPlayer, MpvPlayer, VlcPlayer, XbmcPlayer]
