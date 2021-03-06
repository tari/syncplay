from PySide import QtGui
from PySide.QtCore import Qt, QSettings, QSize, QPoint, QUrl
from syncplay import utils, constants, version
from syncplay.messages import getMessage
import sys
import time
import urllib
from datetime import datetime
import re
import os
from syncplay.utils import formatTime, sameFilename, sameFilesize, sameFileduration, RoomPasswordProvider, formatSize, isURL
from functools import wraps
from twisted.internet import task, threads
import threading
lastCheckedForUpdates = None

class UserlistItemDelegate(QtGui.QStyledItemDelegate):
    def __init__(self):
        QtGui.QStyledItemDelegate.__init__(self)

    def sizeHint(self, option, index):
        size = QtGui.QStyledItemDelegate.sizeHint(self, option, index)
        if (index.column() == constants.USERLIST_GUI_USERNAME_COLUMN):
            size.setWidth(size.width() + constants.USERLIST_GUI_USERNAME_OFFSET)
        return size

    def paint(self, itemQPainter, optionQStyleOptionViewItem, indexQModelIndex):
        column = indexQModelIndex.column()
        if column == constants.USERLIST_GUI_USERNAME_COLUMN:
            currentQAbstractItemModel = indexQModelIndex.model()
            itemQModelIndex = currentQAbstractItemModel.index(indexQModelIndex.row(), constants.USERLIST_GUI_USERNAME_COLUMN, indexQModelIndex.parent())
            if sys.platform.startswith('win'):
                resourcespath = utils.findWorkingDir() + "\\resources\\"
            else:
                resourcespath = utils.findWorkingDir() + "/resources/"
            controlIconQPixmap = QtGui.QPixmap(resourcespath + "user_key.png")
            tickIconQPixmap = QtGui.QPixmap(resourcespath + "tick.png")
            crossIconQPixmap = QtGui.QPixmap(resourcespath + "cross.png")
            roomController = currentQAbstractItemModel.data(itemQModelIndex, Qt.UserRole + constants.USERITEM_CONTROLLER_ROLE)
            userReady = currentQAbstractItemModel.data(itemQModelIndex, Qt.UserRole + constants.USERITEM_READY_ROLE)

            if roomController and not controlIconQPixmap.isNull():
                itemQPainter.drawPixmap (
                    optionQStyleOptionViewItem.rect.x()+6,
                    optionQStyleOptionViewItem.rect.y(),
                    controlIconQPixmap.scaled(16, 16, Qt.KeepAspectRatio))

            if userReady and not tickIconQPixmap.isNull():
                itemQPainter.drawPixmap (
                    (optionQStyleOptionViewItem.rect.x()-10),
                    optionQStyleOptionViewItem.rect.y(),
                    tickIconQPixmap.scaled(16, 16, Qt.KeepAspectRatio))

            elif userReady == False and not crossIconQPixmap.isNull():
                itemQPainter.drawPixmap (
                    (optionQStyleOptionViewItem.rect.x()-10),
                    optionQStyleOptionViewItem.rect.y(),
                    crossIconQPixmap.scaled(16, 16, Qt.KeepAspectRatio))
            isUserRow = indexQModelIndex.parent() != indexQModelIndex.parent().parent()
            if isUserRow:
                optionQStyleOptionViewItem.rect.setX(optionQStyleOptionViewItem.rect.x()+constants.USERLIST_GUI_USERNAME_OFFSET)
        if column == constants.USERLIST_GUI_FILENAME_COLUMN:
            if sys.platform.startswith('win'):
                resourcespath = utils.findWorkingDir() + "\\resources\\"
            else:
                resourcespath = utils.findWorkingDir() + "/resources/"
            currentQAbstractItemModel = indexQModelIndex.model()
            itemQModelIndex = currentQAbstractItemModel.index(indexQModelIndex.row(), constants.USERLIST_GUI_FILENAME_COLUMN, indexQModelIndex.parent())
            fileSwitchRole = currentQAbstractItemModel.data(itemQModelIndex, Qt.UserRole + constants.FILEITEM_SWITCH_ROLE)
            if fileSwitchRole == constants.FILEITEM_SWITCH_FILE_SWITCH:
                fileSwitchIconQPixmap = QtGui.QPixmap(resourcespath + "film_go.png")
                itemQPainter.drawPixmap (
                    (optionQStyleOptionViewItem.rect.x()),
                    optionQStyleOptionViewItem.rect.y(),
                    fileSwitchIconQPixmap.scaled(16, 16, Qt.KeepAspectRatio))
                optionQStyleOptionViewItem.rect.setX(optionQStyleOptionViewItem.rect.x()+16)

            elif fileSwitchRole == constants.FILEITEM_SWITCH_STREAM_SWITCH:
                streamSwitchIconQPixmap = QtGui.QPixmap(resourcespath + "world_go.png")
                itemQPainter.drawPixmap (
                    (optionQStyleOptionViewItem.rect.x()),
                    optionQStyleOptionViewItem.rect.y(),
                    streamSwitchIconQPixmap.scaled(16, 16, Qt.KeepAspectRatio))
                optionQStyleOptionViewItem.rect.setX(optionQStyleOptionViewItem.rect.x()+16)
        QtGui.QStyledItemDelegate.paint(self, itemQPainter, optionQStyleOptionViewItem, indexQModelIndex)

class MainWindow(QtGui.QMainWindow):
    class FileSwitchManager(object):
        def __init__(self):
            self.fileSwitchTimer = task.LoopingCall(self.updateInfo)
            self.fileSwitchTimer.start(constants.FOLDER_SEARCH_DOUBLE_CHECK_INTERVAL, True)

        mediaFilesCache = {}
        filenameWatchlist = []
        currentDirectory = None
        mediaDirectories = None
        lock = threading.Lock()
        client = None
        currentWindow = None
        folderSearchEnabled = True
        disabledDir = None
        newInfo = False
        currentlyUpdating = False

        @staticmethod
        def setWindow(window):
            MainWindow.FileSwitchManager.currentWindow = window

        @staticmethod
        def setClient(newClient):
            MainWindow.FileSwitchManager.client = newClient

        @staticmethod
        def setCurrentDirectory(curDir):
            MainWindow.FileSwitchManager.currentDirectory = curDir
            MainWindow.FileSwitchManager.updateInfo()

        @staticmethod
        def setMediaDirectories(mediaDirs):
            MainWindow.FileSwitchManager.mediaDirectories = mediaDirs
            MainWindow.FileSwitchManager.updateInfo()

        @staticmethod
        def checkForUpdate(self=None):
            if MainWindow.FileSwitchManager.newInfo:
                MainWindow.FileSwitchManager.newInfo = False
                MainWindow.FileSwitchManager.infoUpdated()

        @staticmethod
        def updateInfo():
            if len(MainWindow.FileSwitchManager.filenameWatchlist) > 0 or len(MainWindow.FileSwitchManager.mediaFilesCache) == 0 and MainWindow.FileSwitchManager.currentlyUpdating == False:
                threads.deferToThread(MainWindow.FileSwitchManager._updateInfoThread).addCallback(MainWindow.FileSwitchManager.checkForUpdate)

        @staticmethod
        def setFilenameWatchlist(unfoundFilenames):
            MainWindow.FileSwitchManager.filenameWatchlist = unfoundFilenames

        @staticmethod
        def _updateInfoThread():
            if not MainWindow.FileSwitchManager.folderSearchEnabled:
                if MainWindow.FileSwitchManager.areWatchedFilenamesInCurrentDir():
                    MainWindow.FileSwitchManager.newInfo = True
                return

            with MainWindow.FileSwitchManager.lock:
                try:
                    MainWindow.FileSwitchManager.currentlyUpdating = True
                    dirsToSearch = MainWindow.FileSwitchManager.mediaDirectories

                    if dirsToSearch:
                        newMediaFilesCache = {}
                        startTime = time.time()
                        for directory in dirsToSearch:
                            for root, dirs, files in os.walk(directory):
                                newMediaFilesCache[root] = files
                                if time.time() - startTime > constants.FOLDER_SEARCH_TIMEOUT:
                                    if MainWindow.FileSwitchManager.client is not None and MainWindow.FileSwitchManager.currentWindow is not None:
                                        MainWindow.FileSwitchManager.disabledDir = directory
                                        MainWindow.FileSwitchManager.folderSearchEnabled = False
                                        if MainWindow.FileSwitchManager.areWatchedFilenamesInCurrentDir():
                                            MainWindow.FileSwitchManager.newInfo = True
                                        return

                        if MainWindow.FileSwitchManager.mediaFilesCache <> newMediaFilesCache:
                            MainWindow.FileSwitchManager.mediaFilesCache = newMediaFilesCache
                            MainWindow.FileSwitchManager.newInfo = True
                        elif MainWindow.FileSwitchManager.areWatchedFilenamesInCurrentDir():
                            MainWindow.FileSwitchManager.newInfo = True
                finally:
                    MainWindow.FileSwitchManager.currentlyUpdating = False

        @staticmethod
        def infoUpdated():
            if MainWindow.FileSwitchManager.areWatchedFilenamesInCache() or MainWindow.FileSwitchManager.areWatchedFilenamesInCurrentDir():
                MainWindow.FileSwitchManager.updateListOfWhoIsPlayingWhat()

        @staticmethod
        def updateListOfWhoIsPlayingWhat():
            if MainWindow.FileSwitchManager.client is not None:
                MainWindow.FileSwitchManager.client.showUserList()

        @staticmethod
        def findFilepath(filename):
            if filename is None:
                return

            if MainWindow.FileSwitchManager.currentDirectory is not None:
                candidatePath = os.path.join(MainWindow.FileSwitchManager.currentDirectory,filename)
                if os.path.isfile(candidatePath):
                    return candidatePath

            if MainWindow.FileSwitchManager.mediaFilesCache is not None:
                for directory in MainWindow.FileSwitchManager.mediaFilesCache:
                    files = MainWindow.FileSwitchManager.mediaFilesCache[directory]

                    if len(files) > 0 and filename in files:
                        filepath = os.path.join(directory, filename)
                        if os.path.isfile(filepath):
                            return filepath

        @staticmethod
        def areWatchedFilenamesInCurrentDir():
            if MainWindow.FileSwitchManager.filenameWatchlist is not None and MainWindow.FileSwitchManager.currentDirectory is not None:
                for filename in MainWindow.FileSwitchManager.filenameWatchlist:
                    potentialPath = os.path.join(MainWindow.FileSwitchManager.currentDirectory,filename)
                    if os.path.isfile(potentialPath):
                        return True

        @staticmethod
        def areWatchedFilenamesInCache():
            if MainWindow.FileSwitchManager.filenameWatchlist is not None:
                for filename in MainWindow.FileSwitchManager.filenameWatchlist:
                    if MainWindow.FileSwitchManager.isFilenameInCache(filename):
                        return True

        @staticmethod
        def isFilenameInCurrentDir(filename):
            if filename is not None and MainWindow.FileSwitchManager.currentDirectory is not None:
                potentialPath = os.path.join(MainWindow.FileSwitchManager.currentDirectory,filename)
                if os.path.isfile(potentialPath):
                    return True

        @staticmethod
        def isFilenameInCache(filename):
            if filename is not None and MainWindow.FileSwitchManager.mediaFilesCache is not None:
                for directory in MainWindow.FileSwitchManager.mediaFilesCache:
                    files = MainWindow.FileSwitchManager.mediaFilesCache[directory]
                    if filename in files:
                        return True

    class topSplitter(QtGui.QSplitter):
        def createHandle(self):
            return self.topSplitterHandle(self.orientation(), self)

        class topSplitterHandle(QtGui.QSplitterHandle):
            def mouseReleaseEvent(self, event):
                QtGui.QSplitterHandle.mouseReleaseEvent(self, event)
                self.parent().parent().parent().updateListGeometry()

            def mouseMoveEvent(self, event):
                QtGui.QSplitterHandle.mouseMoveEvent(self, event)
                self.parent().parent().parent().updateListGeometry()

    def needsClient(f):  # @NoSelf
        @wraps(f)
        def wrapper(self, *args, **kwds):
            if not self._syncplayClient:
                self.showDebugMessage("Tried to use client before it was ready!")
                return
            return f(self, *args, **kwds)
        return wrapper

    def addClient(self, client):
        self._syncplayClient = client
        MainWindow.FileSwitchManager.setClient(client)
        self.roomInput.setText(self._syncplayClient.getRoom())
        self.config = self._syncplayClient.getConfig()
        try:
            self.FileSwitchManager.setMediaDirectories(self.config["mediaSearchDirectories"])
            self.updateReadyState(self.config['readyAtStart'])
            autoplayInitialState = self.config['autoplayInitialState']
            if autoplayInitialState is not None:
                self.autoplayPushButton.blockSignals(True)
                self.autoplayPushButton.setChecked(autoplayInitialState)
                self.autoplayPushButton.blockSignals(False)
            if self.config['autoplayMinUsers'] > 1:
                self.autoplayThresholdSpinbox.blockSignals(True)
                self.autoplayThresholdSpinbox.setValue(self.config['autoplayMinUsers'])
                self.autoplayThresholdSpinbox.blockSignals(False)
            self.changeAutoplayState()
            self.changeAutoplayThreshold()
            self.updateAutoPlayIcon()
        except:
            self.showErrorMessage("Failed to load some settings.")
        self.automaticUpdateCheck()

    def promptFor(self, prompt=">", message=""):
        # TODO: Prompt user
        return None

    def showMessage(self, message, noTimestamp=False):
        message = unicode(message)
        message = message.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        message = message.replace("&lt;", "<span style=\"{}\">&lt;".format(constants.STYLE_USERNAME))
        message = message.replace("&gt;", "&gt;</span>")
        message = message.replace("\n", "<br />")
        if noTimestamp:
            self.newMessage(u"{}<br />".format(message))
        else:
            self.newMessage(time.strftime(constants.UI_TIME_FORMAT, time.localtime()) + message + "<br />")

    def getFileSwitchState(self, filename):
        if filename:
            if filename == getMessage("nofile-note"):
                return constants.FILEITEM_SWITCH_NO_SWITCH
            if self._syncplayClient.userlist.currentUser.file and filename == self._syncplayClient.userlist.currentUser.file['name']:
                return constants.FILEITEM_SWITCH_NO_SWITCH
            if isURL(filename):
                return constants.FILEITEM_SWITCH_STREAM_SWITCH
            elif filename not in self.newWatchlist:
                if MainWindow.FileSwitchManager.findFilepath(filename):
                    return constants.FILEITEM_SWITCH_FILE_SWITCH
                else:
                    self.newWatchlist.extend([filename])
        return constants.FILEITEM_SWITCH_NO_SWITCH

    def showUserList(self, currentUser, rooms):
        self._usertreebuffer = QtGui.QStandardItemModel()
        self._usertreebuffer.setHorizontalHeaderLabels(
            (getMessage("roomuser-heading-label"), getMessage("size-heading-label"), getMessage("duration-heading-label"), getMessage("filename-heading-label") ))
        usertreeRoot = self._usertreebuffer.invisibleRootItem()
        if self._syncplayClient.userlist.currentUser.file and self._syncplayClient.userlist.currentUser.file and os.path.isfile(self._syncplayClient.userlist.currentUser.file["path"]):
            MainWindow.FileSwitchManager.setCurrentDirectory(os.path.dirname(self._syncplayClient.userlist.currentUser.file["path"]))

        for room in rooms:
            self.newWatchlist = []
            roomitem = QtGui.QStandardItem(room)
            font = QtGui.QFont()
            font.setItalic(True)
            if room == currentUser.room:
                font.setWeight(QtGui.QFont.Bold)
            roomitem.setFont(font)
            roomitem.setFlags(roomitem.flags() & ~Qt.ItemIsEditable)
            usertreeRoot.appendRow(roomitem)
            isControlledRoom = RoomPasswordProvider.isControlledRoom(room)

            if isControlledRoom:
                if room == currentUser.room and currentUser.isController():
                    roomitem.setIcon(QtGui.QIcon(self.resourcespath + 'lock_open.png'))
                else:
                    roomitem.setIcon(QtGui.QIcon(self.resourcespath + 'lock.png'))
            else:
                roomitem.setIcon(QtGui.QIcon(self.resourcespath + 'chevrons_right.png'))

            for user in rooms[room]:
                useritem = QtGui.QStandardItem(user.username)
                isController = user.isController()
                sameRoom = room == currentUser.room
                if sameRoom:
                    isReadyWithFile = user.isReadyWithFile()
                else:
                    isReadyWithFile = None
                useritem.setData(isController, Qt.UserRole + constants.USERITEM_CONTROLLER_ROLE)
                useritem.setData(isReadyWithFile, Qt.UserRole + constants.USERITEM_READY_ROLE)
                if user.file:
                    filesizeitem = QtGui.QStandardItem(formatSize(user.file['size']))
                    filedurationitem = QtGui.QStandardItem("({})".format(formatTime(user.file['duration'])))
                    filename = user.file['name']
                    if isURL(filename):
                        filename = urllib.unquote(filename)
                    filenameitem = QtGui.QStandardItem(filename)
                    fileSwitchState = self.getFileSwitchState(user.file['name']) if room == currentUser.room else None
                    if fileSwitchState != constants.FILEITEM_SWITCH_NO_SWITCH:
                        filenameTooltip = getMessage("switch-to-file-tooltip").format(filename)
                    else:
                        filenameTooltip = filename
                    filenameitem.setToolTip(filenameTooltip)
                    filenameitem.setData(fileSwitchState, Qt.UserRole + constants.FILEITEM_SWITCH_ROLE)
                    if currentUser.file:
                        sameName = sameFilename(user.file['name'], currentUser.file['name'])
                        sameSize = sameFilesize(user.file['size'], currentUser.file['size'])
                        sameDuration = sameFileduration(user.file['duration'], currentUser.file['duration'])
                        underlinefont = QtGui.QFont()
                        underlinefont.setUnderline(True)
                        if sameRoom:
                            if not sameName:
                                filenameitem.setForeground(QtGui.QBrush(QtGui.QColor(constants.STYLE_DIFFERENTITEM_COLOR)))
                                filenameitem.setFont(underlinefont)
                            if not sameSize:
                                if currentUser.file is not None and formatSize(user.file['size']) == formatSize(currentUser.file['size']):
                                    filesizeitem = QtGui.QStandardItem(formatSize(user.file['size'],precise=True))
                                filesizeitem.setFont(underlinefont)
                                filesizeitem.setForeground(QtGui.QBrush(QtGui.QColor(constants.STYLE_DIFFERENTITEM_COLOR)))
                            if not sameDuration:
                                filedurationitem.setForeground(QtGui.QBrush(QtGui.QColor(constants.STYLE_DIFFERENTITEM_COLOR)))
                                filedurationitem.setFont(underlinefont)
                else:
                    filenameitem = QtGui.QStandardItem(getMessage("nofile-note"))
                    filedurationitem = QtGui.QStandardItem("")
                    filesizeitem = QtGui.QStandardItem("")
                    if room == currentUser.room:
                        filenameitem.setForeground(QtGui.QBrush(QtGui.QColor(constants.STYLE_NOFILEITEM_COLOR)))
                font = QtGui.QFont()
                if currentUser.username == user.username:
                    font.setWeight(QtGui.QFont.Bold)
                    self.updateReadyState(currentUser.isReadyWithFile())
                if isControlledRoom and not isController:
                    useritem.setForeground(QtGui.QBrush(QtGui.QColor(constants.STYLE_NOTCONTROLLER_COLOR)))
                useritem.setFont(font)
                useritem.setFlags(useritem.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsSelectable)
                filenameitem.setFlags(filenameitem.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsSelectable)
                filesizeitem.setFlags(filesizeitem.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsSelectable)
                filedurationitem.setFlags(filedurationitem.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsSelectable)
                roomitem.appendRow((useritem, filesizeitem, filedurationitem, filenameitem))
        self.listTreeModel = self._usertreebuffer
        self.listTreeView.setModel(self.listTreeModel)
        self.listTreeView.setItemDelegate(UserlistItemDelegate())
        self.listTreeView.setItemsExpandable(False)
        self.listTreeView.setRootIsDecorated(False)
        self.listTreeView.expandAll()
        self.updateListGeometry()
        MainWindow.FileSwitchManager.setFilenameWatchlist(self.newWatchlist)
        self.checkForDisabledDir()

    def checkForDisabledDir(self):
        if MainWindow.FileSwitchManager.disabledDir is not None and MainWindow.FileSwitchManager.currentWindow is not None:
            self.showErrorMessage(getMessage("folder-search-timeout-error").format(MainWindow.FileSwitchManager.disabledDir))
            MainWindow.FileSwitchManager.disabledDir = None

    def updateListGeometry(self):
        try:
            roomtocheck = 0
            while self.listTreeModel.item(roomtocheck):
                self.listTreeView.setFirstColumnSpanned(roomtocheck, self.listTreeView.rootIndex(), True)
                roomtocheck += 1
            self.listTreeView.header().setStretchLastSection(False)
            self.listTreeView.header().setResizeMode(0, QtGui.QHeaderView.ResizeToContents)
            self.listTreeView.header().setResizeMode(1, QtGui.QHeaderView.ResizeToContents)
            self.listTreeView.header().setResizeMode(2, QtGui.QHeaderView.ResizeToContents)
            self.listTreeView.header().setResizeMode(3, QtGui.QHeaderView.ResizeToContents)
            NarrowTabsWidth = self.listTreeView.header().sectionSize(0)+self.listTreeView.header().sectionSize(1)+self.listTreeView.header().sectionSize(2)
            if self.listTreeView.header().width() < (NarrowTabsWidth+self.listTreeView.header().sectionSize(3)):
                self.listTreeView.header().resizeSection(3,self.listTreeView.header().width()-NarrowTabsWidth)
            else:
                self.listTreeView.header().setResizeMode(3, QtGui.QHeaderView.Stretch)
            self.listTreeView.expandAll()
        except:
            pass

    def updateReadyState(self, newState):
        oldState = self.readyPushButton.isChecked()
        if newState != oldState and newState != None:
            self.readyPushButton.blockSignals(True)
            self.readyPushButton.setChecked(newState)
            self.readyPushButton.blockSignals(False)
        self.updateReadyIcon()

    def roomClicked(self, item):
        username = item.sibling(item.row(), 0).data()
        filename = item.sibling(item.row(), 3).data()
        while item.parent().row() != -1:
            item = item.parent()
        roomToJoin = item.sibling(item.row(), 0).data()
        if roomToJoin <> self._syncplayClient.getRoom():
            self.joinRoom(item.sibling(item.row(), 0).data())
        elif username and filename and username <> self._syncplayClient.userlist.currentUser.username:
            if self._syncplayClient.userlist.currentUser.file and filename == self._syncplayClient.userlist.currentUser.file:
                return
            if isURL(filename):
                self._syncplayClient._player.openFile(filename)
            else:
                pathFound = MainWindow.FileSwitchManager.findFilepath(filename)
                if pathFound:
                    self._syncplayClient._player.openFile(pathFound)
                else:
                    MainWindow.FileSwitchManager.updateInfo()
                    self.showErrorMessage(getMessage("switch-file-not-found-error").format(filename))

    @needsClient
    def userListChange(self):
        self._syncplayClient.showUserList()

    def updateRoomName(self, room=""):
        self.roomInput.setText(room)

    def showDebugMessage(self, message):
        print(message)

    def showErrorMessage(self, message, criticalerror=False):
        message = unicode(message)
        if criticalerror:
            QtGui.QMessageBox.critical(self, "Syncplay", message)
        message = message.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        message = message.replace("\n", "<br />")
        message = "<span style=\"{}\">".format(constants.STYLE_ERRORNOTIFICATION) + message + "</span>"
        self.newMessage(time.strftime(constants.UI_TIME_FORMAT, time.localtime()) + message + "<br />")

    @needsClient
    def joinRoom(self, room=None):
        if room == None:
            room = self.roomInput.text()
        if room == "":
            if self._syncplayClient.userlist.currentUser.file:
                room = self._syncplayClient.userlist.currentUser.file["name"]
            else:
                room = self._syncplayClient.defaultRoom
        self.roomInput.setText(room)
        if room != self._syncplayClient.getRoom():
            self._syncplayClient.setRoom(room, resetAutoplay=True)
            self._syncplayClient.sendRoom()

    def seekPositionDialog(self):
        seekTime, ok = QtGui.QInputDialog.getText(self, getMessage("seektime-menu-label"),
                                                   getMessage("seektime-msgbox-label"), QtGui.QLineEdit.Normal,
                                                   "0:00")
        if ok and seekTime != '':
            self.seekPosition(seekTime)

    def seekFromButton(self):
        self.seekPosition(self.seekInput.text())

    @needsClient
    def seekPosition(self, seekTime):
        s = re.match(constants.UI_SEEK_REGEX, seekTime)
        if s:
            sign = self._extractSign(s.group('sign'))
            t = utils.parseTime(s.group('time'))
            if t is None:
                return
            if sign:
                t = self._syncplayClient.getGlobalPosition() + sign * t
            self._syncplayClient.setPosition(t)
        else:
            self.showErrorMessage(getMessage("invalid-seek-value"))

    @needsClient
    def undoSeek(self):
        tmp_pos = self._syncplayClient.getPlayerPosition()
        self._syncplayClient.setPosition(self._syncplayClient.playerPositionBeforeLastSeek)
        self._syncplayClient.playerPositionBeforeLastSeek = tmp_pos

    @needsClient
    def togglePause(self):
        self._syncplayClient.setPaused(not self._syncplayClient.getPlayerPaused())

    @needsClient
    def play(self):
        self._syncplayClient.setPaused(False)

    @needsClient
    def pause(self):
        self._syncplayClient.setPaused(True)

    @needsClient
    def exitSyncplay(self):
        self._syncplayClient.stop()

    def closeEvent(self, event):
        self.exitSyncplay()
        self.saveSettings()

    def loadMediaBrowseSettings(self):
        settings = QSettings("Syncplay", "MediaBrowseDialog")
        settings.beginGroup("MediaBrowseDialog")
        self.mediadirectory = settings.value("mediadir", "")
        settings.endGroup()

    def saveMediaBrowseSettings(self):
        settings = QSettings("Syncplay", "MediaBrowseDialog")
        settings.beginGroup("MediaBrowseDialog")
        settings.setValue("mediadir", self.mediadirectory)
        settings.endGroup()

    @needsClient
    def browseMediapath(self):
        if self._syncplayClient._player.customOpenDialog == True:
            self._syncplayClient._player.openCustomOpenDialog()
            return

        self.loadMediaBrowseSettings()
        options = QtGui.QFileDialog.Options()
        self.mediadirectory = ""
        currentdirectory = os.path.dirname(self._syncplayClient.userlist.currentUser.file["path"]) if self._syncplayClient.userlist.currentUser.file else None
        if currentdirectory and os.path.isdir(currentdirectory):
            defaultdirectory = currentdirectory
        elif self.config["mediaSearchDirectories"] and os.path.isdir(self.config["mediaSearchDirectories"][0]):
            defaultdirectory = self.config["mediaSearchDirectories"][0]
        elif os.path.isdir(self.mediadirectory):
            defaultdirectory = self.mediadirectory
        elif os.path.isdir(QtGui.QDesktopServices.storageLocation(QtGui.QDesktopServices.MoviesLocation)):
            defaultdirectory = QtGui.QDesktopServices.storageLocation(QtGui.QDesktopServices.MoviesLocation)
        elif os.path.isdir(QtGui.QDesktopServices.storageLocation(QtGui.QDesktopServices.HomeLocation)):
            defaultdirectory = QtGui.QDesktopServices.storageLocation(QtGui.QDesktopServices.HomeLocation)
        else:
            defaultdirectory = ""
        browserfilter = "All files (*)"
        fileName, filtr = QtGui.QFileDialog.getOpenFileName(self, getMessage("browseformedia-label"), defaultdirectory,
                                                            browserfilter, "", options)
        if fileName:
            if sys.platform.startswith('win'):
                fileName = fileName.replace("/", "\\")
            self.mediadirectory = os.path.dirname(fileName)
            self.FileSwitchManager.setCurrentDirectory(self.mediadirectory)
            self.saveMediaBrowseSettings()
            self._syncplayClient._player.openFile(fileName)

    @needsClient
    def promptForStreamURL(self):
        streamURL, ok = QtGui.QInputDialog.getText(self, getMessage("promptforstreamurl-msgbox-label"),
                                                   getMessage("promptforstreamurlinfo-msgbox-label"), QtGui.QLineEdit.Normal,
                                                   "")
        if ok and streamURL != '':
            self._syncplayClient._player.openFile(streamURL)

    @needsClient
    def createControlledRoom(self):
        controlroom, ok = QtGui.QInputDialog.getText(self, getMessage("createcontrolledroom-msgbox-label"),
                getMessage("controlledroominfo-msgbox-label"), QtGui.QLineEdit.Normal,
                utils.stripRoomName(self._syncplayClient.getRoom()))
        if ok and controlroom != '':
            self._syncplayClient.createControlledRoom(controlroom)

    @needsClient
    def identifyAsController(self):
        msgboxtitle = getMessage("identifyascontroller-msgbox-label")
        msgboxtext = getMessage("identifyinfo-msgbox-label")
        controlpassword, ok = QtGui.QInputDialog.getText(self, msgboxtitle, msgboxtext, QtGui.QLineEdit.Normal, "")
        if ok and controlpassword != '':
            self._syncplayClient.identifyAsController(controlpassword)

    def _extractSign(self, m):
        if m:
            if m == "-":
                return -1
            else:
                return 1
        else:
            return None

    @needsClient
    def setOffset(self):
        newoffset, ok = QtGui.QInputDialog.getText(self, getMessage("setoffset-msgbox-label"),
                                                   getMessage("offsetinfo-msgbox-label"), QtGui.QLineEdit.Normal,
                                                   "")
        if ok and newoffset != '':
            o = re.match(constants.UI_OFFSET_REGEX, "o " + newoffset)
            if o:
                sign = self._extractSign(o.group('sign'))
                t = utils.parseTime(o.group('time'))
                if t is None:
                    return
                if o.group('sign') == "/":
                    t = self._syncplayClient.getPlayerPosition() - t
                elif sign:
                    t = self._syncplayClient.getUserOffset() + sign * t
                self._syncplayClient.setUserOffset(t)
            else:
                self.showErrorMessage(getMessage("invalid-offset-value"))

    def openUserGuide(self):
        if sys.platform.startswith('linux'):
            self.QtGui.QDesktopServices.openUrl(QUrl("http://syncplay.pl/guide/linux/"))
        elif sys.platform.startswith('win'):
            self.QtGui.QDesktopServices.openUrl(QUrl("http://syncplay.pl/guide/windows/"))
        else:
            self.QtGui.QDesktopServices.openUrl(QUrl("http://syncplay.pl/guide/"))

    def drop(self):
        self.close()

    def addTopLayout(self, window):
        window.topSplit = self.topSplitter(Qt.Horizontal, self)

        window.outputLayout = QtGui.QVBoxLayout()
        window.outputbox = QtGui.QTextBrowser()
        window.outputbox.setReadOnly(True)
        window.outputbox.setTextInteractionFlags(window.outputbox.textInteractionFlags() | Qt.TextSelectableByKeyboard)
        window.outputbox.setOpenExternalLinks(True)
        window.outputbox.unsetCursor()
        window.outputbox.moveCursor(QtGui.QTextCursor.End)
        window.outputbox.insertHtml(constants.STYLE_CONTACT_INFO.format(getMessage("contact-label")))
        window.outputbox.moveCursor(QtGui.QTextCursor.End)
        window.outputbox.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

        window.outputlabel = QtGui.QLabel(getMessage("notifications-heading-label"))
        window.outputFrame = QtGui.QFrame()
        window.outputFrame.setLineWidth(0)
        window.outputFrame.setMidLineWidth(0)
        window.outputLayout.setContentsMargins(0, 0, 0, 0)
        window.outputLayout.addWidget(window.outputlabel)
        window.outputLayout.addWidget(window.outputbox)
        window.outputFrame.setLayout(window.outputLayout)

        window.listLayout = QtGui.QVBoxLayout()
        window.listTreeModel = QtGui.QStandardItemModel()
        window.listTreeView = QtGui.QTreeView()
        window.listTreeView.setModel(window.listTreeModel)
        window.listTreeView.setIndentation(21)
        window.listTreeView.doubleClicked.connect(self.roomClicked)
        window.listlabel = QtGui.QLabel(getMessage("userlist-heading-label"))
        window.listFrame = QtGui.QFrame()
        window.listFrame.setLineWidth(0)
        window.listFrame.setMidLineWidth(0)
        window.listFrame.setSizePolicy(QtGui.QSizePolicy.Preferred, QtGui.QSizePolicy.Preferred)
        window.listLayout.setContentsMargins(0, 0, 0, 0)
        window.listLayout.addWidget(window.listlabel)
        window.listLayout.addWidget(window.listTreeView)

        window.roomInput = QtGui.QLineEdit()
        window.roomInput.returnPressed.connect(self.joinRoom)
        window.roomButton = QtGui.QPushButton(QtGui.QIcon(self.resourcespath + 'door_in.png'),
                                              getMessage("joinroom-menu-label"))
        window.roomButton.pressed.connect(self.joinRoom)
        window.roomLayout = QtGui.QHBoxLayout()
        window.roomFrame = QtGui.QFrame()
        window.roomFrame.setLayout(self.roomLayout)
        window.roomFrame.setContentsMargins(0,0,0,0)
        window.roomFrame.setSizePolicy(QtGui.QSizePolicy.Minimum, QtGui.QSizePolicy.Minimum)
        window.roomLayout.setContentsMargins(0,0,0,0)
        self.roomButton.setToolTip(getMessage("joinroom-tooltip"))
        window.roomLayout.addWidget(window.roomInput)
        window.roomLayout.addWidget(window.roomButton)
        window.roomFrame.setMaximumHeight(window.roomFrame.sizeHint().height())
        window.listLayout.addWidget(window.roomFrame, Qt.AlignRight)

        window.listFrame.setLayout(window.listLayout)

        window.topSplit.addWidget(window.outputFrame)
        window.topSplit.addWidget(window.listFrame)
        window.topSplit.setStretchFactor(0,4)
        window.topSplit.setStretchFactor(1,5)
        window.mainLayout.addWidget(window.topSplit)
        window.topSplit.setSizePolicy(QtGui.QSizePolicy.Preferred, QtGui.QSizePolicy.Expanding)

    def addBottomLayout(self, window):
        window.bottomLayout = QtGui.QHBoxLayout()
        window.bottomFrame = QtGui.QFrame()
        window.bottomFrame.setLayout(window.bottomLayout)
        window.bottomLayout.setContentsMargins(0,0,0,0)

        self.addPlaybackLayout(window)

        window.readyPushButton = QtGui.QPushButton()
        readyFont = QtGui.QFont()
        readyFont.setWeight(QtGui.QFont.Bold)
        window.readyPushButton.setText(getMessage("ready-guipushbuttonlabel"))
        window.readyPushButton.setCheckable(True)
        window.readyPushButton.setAutoExclusive(False)
        window.readyPushButton.toggled.connect(self.changeReadyState)
        window.readyPushButton.setFont(readyFont)
        window.readyPushButton.setStyleSheet(constants.STYLE_READY_PUSHBUTTON)
        window.readyPushButton.setToolTip(getMessage("ready-tooltip"))
        window.listLayout.addWidget(window.readyPushButton, Qt.AlignRight)

        window.autoplayLayout = QtGui.QHBoxLayout()
        window.autoplayFrame = QtGui.QFrame()
        window.autoplayFrame.setVisible(False)
        window.autoplayLayout.setContentsMargins(0,0,0,0)
        window.autoplayFrame.setLayout(window.autoplayLayout)
        window.autoplayPushButton = QtGui.QPushButton()
        autoPlayFont = QtGui.QFont()
        autoPlayFont.setWeight(QtGui.QFont.Bold)
        window.autoplayPushButton.setText(getMessage("autoplay-guipushbuttonlabel"))
        window.autoplayPushButton.setCheckable(True)
        window.autoplayPushButton.setAutoExclusive(False)
        window.autoplayPushButton.toggled.connect(self.changeAutoplayState)
        window.autoplayPushButton.setFont(autoPlayFont)
        window.autoplayPushButton.setStyleSheet(constants.STYLE_AUTO_PLAY_PUSHBUTTON)
        window.autoplayPushButton.setSizePolicy(QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Expanding)
        window.autoplayPushButton.setToolTip(getMessage("autoplay-tooltip"))
        window.autoplayLabel = QtGui.QLabel(getMessage("autoplay-minimum-label"))
        window.autoplayLabel.setSizePolicy(QtGui.QSizePolicy.Minimum, QtGui.QSizePolicy.Minimum)
        window.autoplayLabel.setMaximumWidth(window.autoplayLabel.minimumSizeHint().width())
        window.autoplayLabel.setToolTip(getMessage("autoplay-tooltip"))
        window.autoplayThresholdSpinbox = QtGui.QSpinBox()
        window.autoplayThresholdSpinbox.setMaximumWidth(window.autoplayThresholdSpinbox.minimumSizeHint().width())
        window.autoplayThresholdSpinbox.setMinimum(2)
        window.autoplayThresholdSpinbox.setMaximum(99)
        window.autoplayThresholdSpinbox.setToolTip(getMessage("autoplay-tooltip"))
        window.autoplayThresholdSpinbox.valueChanged.connect(self.changeAutoplayThreshold)
        window.autoplayLayout.addWidget(window.autoplayPushButton, Qt.AlignRight)
        window.autoplayLayout.addWidget(window.autoplayLabel, Qt.AlignRight)
        window.autoplayLayout.addWidget(window.autoplayThresholdSpinbox, Qt.AlignRight)

        window.listLayout.addWidget(window.autoplayFrame, Qt.AlignLeft)
        window.autoplayFrame.setMaximumHeight(window.autoplayFrame.sizeHint().height())
        window.mainLayout.addWidget(window.bottomFrame, Qt.AlignLeft)
        window.bottomFrame.setMaximumHeight(window.bottomFrame.minimumSizeHint().height())

    def addPlaybackLayout(self, window):
        window.playbackFrame = QtGui.QFrame()
        window.playbackFrame.setVisible(False)
        window.playbackFrame.setContentsMargins(0,0,0,0)
        window.playbackLayout = QtGui.QHBoxLayout()
        window.playbackLayout.setAlignment(Qt.AlignLeft)
        window.playbackLayout.setContentsMargins(0,0,0,0)
        window.playbackFrame.setLayout(window.playbackLayout)
        window.seekInput = QtGui.QLineEdit()
        window.seekInput.returnPressed.connect(self.seekFromButton)
        window.seekButton = QtGui.QPushButton(QtGui.QIcon(self.resourcespath + 'clock_go.png'), "")
        window.seekButton.setToolTip(getMessage("seektime-menu-label"))
        window.seekButton.pressed.connect(self.seekFromButton)
        window.seekInput.setText("0:00")
        window.seekInput.setFixedWidth(60)
        window.playbackLayout.addWidget(window.seekInput)
        window.playbackLayout.addWidget(window.seekButton)
        window.unseekButton = QtGui.QPushButton(QtGui.QIcon(self.resourcespath + 'arrow_undo.png'), "")
        window.unseekButton.setToolTip(getMessage("undoseek-menu-label"))
        window.unseekButton.pressed.connect(self.undoSeek)

        window.miscLayout = QtGui.QHBoxLayout()
        window.playbackLayout.addWidget(window.unseekButton)
        window.playButton = QtGui.QPushButton(QtGui.QIcon(self.resourcespath + 'control_play_blue.png'), "")
        window.playButton.setToolTip(getMessage("play-menu-label"))
        window.playButton.pressed.connect(self.play)
        window.playbackLayout.addWidget(window.playButton)
        window.pauseButton = QtGui.QPushButton(QtGui.QIcon(self.resourcespath + 'control_pause_blue.png'), "")
        window.pauseButton.setToolTip(getMessage("pause-menu-label"))
        window.pauseButton.pressed.connect(self.pause)
        window.playbackLayout.addWidget(window.pauseButton)
        window.playbackFrame.setMaximumHeight(window.playbackFrame.sizeHint().height())
        window.playbackFrame.setMaximumWidth(window.playbackFrame.sizeHint().width())
        window.outputLayout.addWidget(window.playbackFrame)

    def addMenubar(self, window):
        window.menuBar = QtGui.QMenuBar()

        # File menu

        window.fileMenu = QtGui.QMenu(getMessage("file-menu-label"), self)
        window.openAction = window.fileMenu.addAction(QtGui.QIcon(self.resourcespath + 'folder_explore.png'),
                                                      getMessage("openmedia-menu-label"))
        window.openAction.triggered.connect(self.browseMediapath)
        window.openAction = window.fileMenu.addAction(QtGui.QIcon(self.resourcespath + 'world_explore.png'),
                                                      getMessage("openstreamurl-menu-label"))
        window.openAction.triggered.connect(self.promptForStreamURL)

        window.exitAction = window.fileMenu.addAction(QtGui.QIcon(self.resourcespath + 'cross.png'),
                                                      getMessage("exit-menu-label"))
        window.exitAction.triggered.connect(self.exitSyncplay)
        window.menuBar.addMenu(window.fileMenu)

        # Playback menu

        window.playbackMenu = QtGui.QMenu(getMessage("playback-menu-label"), self)
        window.playAction = window.playbackMenu.addAction(QtGui.QIcon(self.resourcespath + 'control_play_blue.png'), getMessage("play-menu-label"))
        window.playAction.triggered.connect(self.play)
        window.pauseAction = window.playbackMenu.addAction(QtGui.QIcon(self.resourcespath + 'control_pause_blue.png'), getMessage("pause-menu-label"))
        window.pauseAction.triggered.connect(self.pause)
        window.seekAction = window.playbackMenu.addAction(QtGui.QIcon(self.resourcespath + 'clock_go.png'), getMessage("seektime-menu-label"))
        window.seekAction.triggered.connect(self.seekPositionDialog)
        window.unseekAction = window.playbackMenu.addAction(QtGui.QIcon(self.resourcespath + 'arrow_undo.png'), getMessage("undoseek-menu-label"))
        window.unseekAction.triggered.connect(self.undoSeek)

        window.menuBar.addMenu(window.playbackMenu)

        # Advanced menu

        window.advancedMenu = QtGui.QMenu(getMessage("advanced-menu-label"), self)
        window.setoffsetAction = window.advancedMenu.addAction(QtGui.QIcon(self.resourcespath + 'timeline_marker.png'),
                                                               getMessage("setoffset-menu-label"))
        window.setoffsetAction.triggered.connect(self.setOffset)

        window.createcontrolledroomAction = window.advancedMenu.addAction(
            QtGui.QIcon(self.resourcespath + 'page_white_key.png'), getMessage("createcontrolledroom-menu-label"))
        window.createcontrolledroomAction.triggered.connect(self.createControlledRoom)
        window.identifyascontroller = window.advancedMenu.addAction(QtGui.QIcon(self.resourcespath + 'key_go.png'),
                                                                    getMessage("identifyascontroller-menu-label"))
        window.identifyascontroller.triggered.connect(self.identifyAsController)

        window.menuBar.addMenu(window.advancedMenu)

        # Window menu

        window.windowMenu = QtGui.QMenu(getMessage("window-menu-label"), self)

        window.playbackAction = window.windowMenu.addAction(getMessage("playbackbuttons-menu-label"))
        window.playbackAction.setCheckable(True)
        window.playbackAction.triggered.connect(self.updatePlaybackFrameVisibility)

        window.autoplayAction = window.windowMenu.addAction(getMessage("autoplay-menu-label"))
        window.autoplayAction.setCheckable(True)
        window.autoplayAction.triggered.connect(self.updateAutoplayVisibility)
        window.menuBar.addMenu(window.windowMenu)


        # Help menu

        window.helpMenu = QtGui.QMenu(getMessage("help-menu-label"), self)
        window.userguideAction = window.helpMenu.addAction(QtGui.QIcon(self.resourcespath + 'help.png'),
                                                           getMessage("userguide-menu-label"))
        window.userguideAction.triggered.connect(self.openUserGuide)
        window.updateAction = window.helpMenu.addAction(QtGui.QIcon(self.resourcespath + 'application_get.png'),
                                                           getMessage("update-menu-label"))
        window.updateAction.triggered.connect(self.userCheckForUpdates)

        window.menuBar.addMenu(window.helpMenu)
        window.mainLayout.setMenuBar(window.menuBar)

    def addMainFrame(self, window):
        window.mainFrame = QtGui.QFrame()
        window.mainFrame.setLineWidth(0)
        window.mainFrame.setMidLineWidth(0)
        window.mainFrame.setContentsMargins(0, 0, 0, 0)
        window.mainFrame.setLayout(window.mainLayout)

        window.setCentralWidget(window.mainFrame)

    def newMessage(self, message):
        self.outputbox.moveCursor(QtGui.QTextCursor.End)
        self.outputbox.insertHtml(message)
        self.outputbox.moveCursor(QtGui.QTextCursor.End)

    def resetList(self):
        self.listbox.setText("")

    def newListItem(self, item):
        self.listbox.moveCursor(QtGui.QTextCursor.End)
        self.listbox.insertHtml(item)
        self.listbox.moveCursor(QtGui.QTextCursor.End)

    def updatePlaybackFrameVisibility(self):
        self.playbackFrame.setVisible(self.playbackAction.isChecked())

    def updateAutoplayVisibility(self):
        self.autoplayFrame.setVisible(self.autoplayAction.isChecked())

    def changeReadyState(self):
        self.updateReadyIcon()
        if self._syncplayClient:
            self._syncplayClient.changeReadyState(self.readyPushButton.isChecked())
        else:
            self.showDebugMessage("Tried to change ready state too soon.")

    @needsClient
    def changeAutoplayThreshold(self, source=None):
        self._syncplayClient.changeAutoPlayThrehsold(self.autoplayThresholdSpinbox.value())

    def updateAutoPlayState(self, newState):
        oldState = self.autoplayPushButton.isChecked()
        if newState != oldState and newState != None:
            self.autoplayPushButton.blockSignals(True)
            self.autoplayPushButton.setChecked(newState)
            self.autoplayPushButton.blockSignals(False)
        self.updateAutoPlayIcon()

    @needsClient
    def changeAutoplayState(self, source=None):
        self.updateAutoPlayIcon()
        if self._syncplayClient:
            self._syncplayClient.changeAutoplayState(self.autoplayPushButton.isChecked())
        else:
            self.showDebugMessage("Tried to set AutoplayState too soon")

    def updateReadyIcon(self):
        ready = self.readyPushButton.isChecked()
        if ready:
            self.readyPushButton.setIcon(QtGui.QIcon(self.resourcespath + 'tick_checkbox.png'))
        else:
            self.readyPushButton.setIcon(QtGui.QIcon(self.resourcespath + 'empty_checkbox.png'))

    def updateAutoPlayIcon(self):
        ready = self.autoplayPushButton.isChecked()
        if ready:
            self.autoplayPushButton.setIcon(QtGui.QIcon(self.resourcespath + 'tick_checkbox.png'))
        else:
            self.autoplayPushButton.setIcon(QtGui.QIcon(self.resourcespath + 'empty_checkbox.png'))

    def automaticUpdateCheck(self):
        currentDateTime = datetime.utcnow()
        if not self.config['checkForUpdatesAutomatically']:
            return
        if self.config['lastCheckedForUpdates']:
            configLastChecked = datetime.strptime(self.config["lastCheckedForUpdates"], "%Y-%m-%d %H:%M:%S.%f")
            if self.lastCheckedForUpdates is None or configLastChecked > self.lastCheckedForUpdates:
                self.lastCheckedForUpdates = configLastChecked
        if self.lastCheckedForUpdates is None:
            self.checkForUpdates()
        else:
            timeDelta = currentDateTime - self.lastCheckedForUpdates
            if timeDelta.total_seconds() > constants.AUTOMATIC_UPDATE_CHECK_FREQUENCY:
                self.checkForUpdates()

    def userCheckForUpdates(self):
        self.checkForUpdates(userInitiated=True)

    @needsClient
    def checkForUpdates(self, userInitiated=False):
        self.lastCheckedForUpdates = datetime.utcnow()
        updateStatus, updateMessage, updateURL, self.publicServerList = self._syncplayClient.checkForUpdate(userInitiated)

        if updateMessage is None:
            if updateStatus == "uptodate":
                updateMessage = getMessage("syncplay-uptodate-notification")
            elif updateStatus == "updateavailale":
                updateMessage = getMessage("syncplay-updateavailable-notification")
            else:
                import syncplay
                updateMessage = getMessage("update-check-failed-notification").format(syncplay.version)
                if userInitiated == True:
                    updateURL = constants.SYNCPLAY_DOWNLOAD_URL
        if updateURL is not None:
            reply = QtGui.QMessageBox.question(self, "Syncplay",
                                        updateMessage, QtGui.QMessageBox.StandardButton.Yes | QtGui.QMessageBox.StandardButton.No)
            if reply == QtGui.QMessageBox.Yes:
                self.QtGui.QDesktopServices.openUrl(QUrl(updateURL))
        elif userInitiated:
            QtGui.QMessageBox.information(self, "Syncplay", updateMessage)
        else:
            self.showMessage(updateMessage)

    def dragEnterEvent(self, event):
        data = event.mimeData()
        urls = data.urls()
        if urls and urls[0].scheme() == 'file':
            event.acceptProposedAction()

    def dropEvent(self, event):
        rewindFile = False
        if QtGui.QDropEvent.proposedAction(event) == Qt.MoveAction:
            QtGui.QDropEvent.setDropAction(event, Qt.CopyAction)  # Avoids file being deleted
            rewindFile = True
        data = event.mimeData()
        urls = data.urls()
        if urls and urls[0].scheme() == 'file':
            dropfilepath = os.path.abspath(unicode(event.mimeData().urls()[0].toLocalFile()))
            if rewindFile == False:
                self._syncplayClient._player.openFile(dropfilepath)
            else:
                self._syncplayClient.setPosition(0)
                self._syncplayClient._player.openFile(dropfilepath, resetPosition=True)
                self._syncplayClient.setPosition(0)

    def saveSettings(self):
        settings = QSettings("Syncplay", "MainWindow")
        settings.beginGroup("MainWindow")
        settings.setValue("size", self.size())
        settings.setValue("pos", self.pos())
        settings.setValue("showPlaybackButtons", self.playbackAction.isChecked())
        settings.setValue("showAutoPlayButton", self.autoplayAction.isChecked())
        settings.setValue("autoplayChecked", self.autoplayPushButton.isChecked())
        settings.setValue("autoplayMinUsers", self.autoplayThresholdSpinbox.value())
        settings.endGroup()
        settings = QSettings("Syncplay", "Interface")
        settings.beginGroup("Update")
        settings.setValue("lastChecked", self.lastCheckedForUpdates)
        settings.endGroup()
        settings.beginGroup("PublicServerList")
        if self.publicServerList:
            settings.setValue("publicServers", self.publicServerList)
        settings.endGroup()

    def loadSettings(self):
        settings = QSettings("Syncplay", "MainWindow")
        settings.beginGroup("MainWindow")
        self.resize(settings.value("size", QSize(700, 500)))
        self.move(settings.value("pos", QPoint(200, 200)))
        if settings.value("showPlaybackButtons", "false") == "true":
            self.playbackAction.setChecked(True)
            self.updatePlaybackFrameVisibility()
        if settings.value("showAutoPlayButton", "false") == "true":
            self.autoplayAction.setChecked(True)
            self.updateAutoplayVisibility()
        if settings.value("autoplayChecked", "false") == "true":
            self.updateAutoPlayState(True)
            self.autoplayPushButton.setChecked(True)
        self.autoplayThresholdSpinbox.blockSignals(True)
        self.autoplayThresholdSpinbox.setValue(int(settings.value("autoplayMinUsers", 2)))
        self.autoplayThresholdSpinbox.blockSignals(False)
        settings.endGroup()
        settings = QSettings("Syncplay", "Interface")
        settings.beginGroup("Update")
        self.lastCheckedForUpdates = settings.value("lastChecked", None)
        settings.endGroup()
        settings.beginGroup("PublicServerList")
        self.publicServerList = settings.value("publicServers", None)

    def __init__(self):
        super(MainWindow, self).__init__()
        FileSwitchManager = self.FileSwitchManager()
        FileSwitchManager.setWindow(self)
        self.newWatchlist = []
        self.publicServerList = []
        self.lastCheckedForUpdates = None
        self._syncplayClient = None
        self.folderSearchEnabled = True
        self.QtGui = QtGui
        if sys.platform.startswith('win'):
            self.resourcespath = utils.findWorkingDir() + "\\resources\\"
        else:
            self.resourcespath = utils.findWorkingDir() + "/resources/"
        self.setWindowFlags(self.windowFlags() & Qt.AA_DontUseNativeMenuBar)
        self.setWindowTitle("Syncplay v" + version)
        self.mainLayout = QtGui.QVBoxLayout()
        self.addTopLayout(self)
        self.addBottomLayout(self)
        self.addMenubar(self)
        self.addMainFrame(self)
        self.loadSettings()
        self.setWindowIcon(QtGui.QIcon(self.resourcespath + "syncplay.png"))
        self.setWindowFlags(self.windowFlags() & Qt.WindowCloseButtonHint & Qt.AA_DontUseNativeMenuBar & Qt.WindowMinimizeButtonHint & ~Qt.WindowContextHelpButtonHint)
        self.show()
        self.setAcceptDrops(True)
