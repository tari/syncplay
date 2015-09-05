#  Syncplay

Solution to synchronize video playback across multiple instances of mplayer2, mpv, Media Player Classic (MPC-HC), VLC and XBMC over the Internet.

## Official website
http://syncplay.pl

## Download
http://syncplay.pl/download/

## (Experimental) XBMC support

XBMC support is experimental, since the player is controlled via a network
connection. This allows you to control the player on another machine (such as
a HTPC), but also means some of SyncPlay's machinery for finding players
doesn't work well.

### Prerequisites

In XBMC's Settings->Services screen, check "Allow control of XBMC via HTTP".
Make a note of the port number and username/password, if set. On the
Remote Control tab in settings, enable one or both of the check boxes according
to where you will be running SyncPlay relative to XBMC.

### Configuration

For the media player path in SyncPlay settings, enter the HTTP URL for your
XBMC. For instance, if running XBMC on the same machine as SyncPlay and with
the default settings:

    http://localhost:8080

Or with a username and password set:

    http://username:password@localhost:8080

Or running on another machine with username and password set an on an unusual
port:

    http://username:password@example.net:12345

If a valid XBMC RPC endpoint is detected, the XBMC icon will appear when you
have entered a complete URL. By default it will search for a server at
localhost on port 8080 with no password, so this will appear without any
intervention if present.

### Caveats

Typing URLs in the configuration GUI can be *very* slow because it attempts to
connect whenever you change the text in that box. It helps to enter the entire
url minus 'http://' first (in which case it's not a valid URL and gives up
quickly), then add 'http://' last.

The SyncPlay file browser *requires* that it be able to read selected files,
so you must have a way to access files via both XBMC and SyncPlay. The
mechanism for translating these paths is currently hard-coded and needs to
be exposed via configuration at the least. Alternately, manual file opening
can be disabled in the client, and it then listens for notifications from
XBMC for when files are opened (this is a cleaner solution). You can adjust
the filename translation logic in the `openFile` method of `XbmcPlayer`
(`syncplay/players/xbmc.py`).

The "Path to media player" field in configuration will remember its value as
long as the XBMC server is available. Otherwise it will be forgotten on
startup- this is a limitation of how SyncPlay detects available players.

## What does it do

Syncplay synchronises the position and play state of multiple media players so that the viewers can watch the same thing at the same time.
This means that when one person pauses/unpauses playback or seeks (jumps position) within their media player then this will be replicated across all media players connected to the same server and in the same 'room' (viewing session).
When a new person joins they will also be synchronised.

## What it doesn't do

Syncplay does not use video streaming or file sharing so each user must have their own copy of the media to be played. Syncplay does not synchronise player configuration, audio/subtitle track choice, playback rate, volume or filters. Furthermore, users must manually choose what file to play as Syncplay does not synchronise which file is open. Finally, Syncplay does not provide a voice or text-based chat platform to allow for discussion during playback as Syncplay is intended to be used in conjunction with third-party communication solutions such as IRC and Mumble.

## Authors
* *Concept and principal Syncplay developer* - Uriziel.
* *Other Syncplay coders* - daniel-123, Et0h.
* *Original SyncPlay code* - Tomasz Kowalczyk (Fluxid), who developed SyncPlay at https://github.com/fluxid/syncplay
