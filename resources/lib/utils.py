import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs

__addon_id__ = 'script.backup.pro'


def _addon():
    # Kodi can rebuild its add-on objects after add-on-manager activity.
    # Never retain one wrapper for the lifetime of the service interpreter.
    return xbmcaddon.Addon(__addon_id__)


def data_dir():
    return _addon().getAddonInfo('profile')


def addon_dir():
    return _addon().getAddonInfo('path')


def openSettings():
    _addon().openSettings()


def log(message, loglevel=xbmc.LOGDEBUG):
    xbmc.log(__addon_id__ + "-" + _addon().getAddonInfo('version') + ": " + message, level=loglevel)


def showNotification(message):
    xbmcgui.Dialog().notification(getString(30010), message, time=4000, icon=xbmcvfs.translatePath(_addon().getAddonInfo('path') + "/resources/images/icon-v2.png"))


def getSetting(name):
    return _addon().getSetting(name)

def getSettingStringStripped(name):
    return _addon().getSettingString(name).strip()

def getSettingBool(name):
    return bool(_addon().getSettingBool(name))


def getSettingInt(name):
    return _addon().getSettingInt(name)


def setSetting(name, value):
    _addon().setSetting(name, value)


def getString(string_id):
    return _addon().getLocalizedString(string_id)


def getRegionalTimestamp(date_time, dateformat=['dateshort']):
    result = ''

    for aFormat in dateformat:
        result = result + ("%s " % date_time.strftime(xbmc.getRegion(aFormat)))

    return result.strip()


def diskString(fSize):
    # convert a size in kilobytes to the best possible match and return as a string
    fSize = float(fSize)
    i = 0
    sizeNames = ['KB', 'MB', 'GB', 'TB']

    while(fSize > 1024 and i < len(sizeNames) - 1):
        fSize = fSize / 1024
        i = i + 1

    return "%0.2f%s" % (fSize, sizeNames[i])
