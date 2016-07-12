# -*- coding: utf-8 -*-

###############################################################################

from urllib import urlencode

import xbmcgui
import xbmcvfs

import clientinfo
import utils

import PlexAPI


###############################################################################


@utils.logging
class PlayUtils():

    def __init__(self, item):

        self.item = item
        self.API = PlexAPI.API(item)

        self.clientInfo = clientinfo.ClientInfo()

        self.userid = utils.window('currUserId')
        self.server = utils.window('pms_server')
        self.machineIdentifier = utils.window('plex_machineIdentifier')

    def getPlayUrl(self, partNumber=None):
        """
        Returns the playurl for the part with number partNumber
        (movie might consist of several files)

        playurl is utf-8 encoded!
        """
        self.API.setPartNumber(partNumber)
        playurl = self.isDirectPlay()

        if playurl is not None:
            self.logMsg("File is direct playing.", 1)
            playurl = utils.tryEncode(playurl)
            # Set playmethod property
            utils.window('emby_%s.playmethod' % playurl, "DirectPlay")

        elif self.isDirectStream():
            self.logMsg("File is direct streaming.", 1)
            playurl = utils.tryEncode(
                self.API.getTranscodeVideoPath('DirectStream'))
            # Set playmethod property
            utils.window('emby_%s.playmethod' % playurl, "DirectStream")

        else:
            self.logMsg("File is transcoding.", 1)
            playurl = utils.tryEncode(self.API.getTranscodeVideoPath(
                'Transcode',
                quality={
                    'maxVideoBitrate': self.getBitrate(),
                    'videoResolution': self.getResolution(),
                    'videoQuality': '100'
                }))
            # Set playmethod property
            utils.window('emby_%s.playmethod' % playurl, value="Transcode")

        self.logMsg("The playurl is: %s" % playurl, 1)
        return playurl

    def httpPlay(self):
        # Audio, Video, Photo

        itemid = self.item['Id']
        mediatype = self.item['MediaType']

        if mediatype == "Audio":
            playurl = "%s/emby/Audio/%s/stream" % (self.server, itemid)
        else:
            playurl = "%s/emby/Videos/%s/stream?static=true" % (self.server, itemid)

        return playurl

    def isDirectPlay(self):
        """
        Returns the path/playurl if we can direct play, None otherwise
        """
        # True for e.g. plex.tv watch later
        if self.API.shouldStream() is True:
            self.logMsg("Plex item optimized for direct streaming", 1)
            return
        # set to either 'Direct Stream=1' or 'Transcode=2'
        # and NOT to 'Direct Play=0'
        if utils.settings('playType') != "0":
            # User forcing to play via HTTP
            self.logMsg("User chose to not direct play", 1)
            return
        if self.mustTranscode():
            return
        return self.API.validatePlayurl(self.API.getFilePath(),
                                        self.API.getType(),
                                        forceCheck=True)

    def directPlay(self):

        try:
            playurl = self.item['MediaSources'][0]['Path']
        except (IndexError, KeyError):
            playurl = self.item['Path']

        if self.item.get('VideoType'):
            # Specific format modification
            if self.item['VideoType'] == "Dvd":
                playurl = "%s/VIDEO_TS/VIDEO_TS.IFO" % playurl
            elif self.item['VideoType'] == "BluRay":
                playurl = "%s/BDMV/index.bdmv" % playurl

        # Assign network protocol
        if playurl.startswith('\\\\'):
            playurl = playurl.replace("\\\\", "smb://")
            playurl = playurl.replace("\\", "/")

        if "apple.com" in playurl:
            USER_AGENT = "QuickTime/7.7.4"
            playurl += "?|User-Agent=%s" % USER_AGENT

        return playurl

    def fileExists(self):

        if 'Path' not in self.item:
            # File has no path defined in server
            return False

        # Convert path to direct play
        path = self.directPlay()
        self.logMsg("Verifying path: %s" % path, 1)

        if xbmcvfs.exists(path):
            self.logMsg("Path exists.", 1)
            return True

        elif ":" not in path:
            self.logMsg("Can't verify path, assumed linux. Still try to direct play.", 1)
            return True

        else:
            self.logMsg("Failed to find file.", 1)
            return False

    def mustTranscode(self):
        """
        Returns True if we need to transcode because
            - codec is in h265
            - 10bit video codec
            - HEVC codec
        if the corresponding file settings are set to 'true'
        """
        videoCodec = self.API.getVideoCodec()
        self.logMsg("videoCodec: %s" % videoCodec, 2)
        if (utils.settings('transcodeHi10P') == 'true' and
                videoCodec['bitDepth'] == '10'):
            self.logMsg('Option to transcode 10bit video content enabled.', 1)
            return True
        codec = videoCodec['videocodec']
        if (utils.settings('transcodeHEVC') == 'true' and codec == 'hevc'):
            self.logMsg('Option to transcode HEVC video codec enabled.', 1)
            return True
        if codec is None:
            # e.g. trailers. Avoids TypeError with "'h265' in codec"
            self.logMsg('No codec from PMS, not transcoding.', 1)
            return False
        try:
            resolution = int(videoCodec['resolution'])
        except TypeError:
            self.logMsg('No video resolution from PMS, not transcoding.', 1)
            return False
        if 'h265' in codec:
            if resolution >= self.getH265():
                self.logMsg("Option to transcode h265 enabled. Resolution of "
                            "the media: %s, transcoding limit resolution: %s"
                            % (str(resolution), str(self.getH265())), 1)
                return True
        return False

    def isDirectStream(self):
        # Never transcode Music
        if self.API.getType() == 'track':
            return True
        # set to 'Transcode=2'
        if utils.settings('playType') == "2":
            # User forcing to play via HTTP
            self.logMsg("User chose to transcode", 1)
            return False
        if self.mustTranscode():
            return False
        # Verify the bitrate
        if not self.isNetworkSufficient():
            self.logMsg("The network speed is insufficient to direct stream "
                        "file. Transcoding", 1)
            return False
        return True

    def isNetworkSufficient(self):
        """
        Returns True if the network is sufficient (set in file settings)
        """
        try:
            sourceBitrate = int(self.API.getDataFromPartOrMedia('bitrate'))
        except:
            self.logMsg('Could not detect source bitrate. It is assumed to be'
                        'sufficient', 1)
            return True
        settings = self.getBitrate()
        self.logMsg("The add-on settings bitrate is: %s, the video bitrate"
                    "required is: %s" % (settings, sourceBitrate), 1)
        if settings < sourceBitrate:
            return False
        return True

    def getBitrate(self):
        # get the addon video quality
        videoQuality = utils.settings('transcoderVideoQualities')
        bitrate = {
            '0': 320,
            '1': 720,
            '2': 1500,
            '3': 2000,
            '4': 3000,
            '5': 4000,
            '6': 8000,
            '7': 10000,
            '8': 12000,
            '9': 20000,
            '10': 40000,
        }
        # max bit rate supported by server (max signed 32bit integer)
        return bitrate.get(videoQuality, 2147483)

    def getH265(self):
        """
        Returns the user settings for transcoding h265: boundary resolutions
        of 480, 720 or 1080 as an int

        OR 9999999 (int) if user chose not to transcode
        """
        H265 = {
            '0': 9999999,
            '1': 480,
            '2': 720,
            '3': 1080
        }
        return H265[utils.settings('transcodeH265')]

    def getResolution(self):
        chosen = utils.settings('transcoderVideoQualities')
        res = {
            '0': '420x420',
            '1': '576x320',
            '2': '720x480',
            '3': '1024x768',
            '4': '1280x720',
            '5': '1280x720',
            '6': '1920x1080',
            '7': '1920x1080',
            '8': '1920x1080',
            '9': '1920x1080',
            '10': '1920x1080',
        }
        return res[chosen]

    def audioSubsPref(self, listitem, url, part=None):
        lang = utils.language
        dialog = xbmcgui.Dialog()
        # For transcoding only
        # Present the list of audio to select from
        audioStreamsList = []
        audioStreams = []
        # audioStreamsChannelsList = []
        subtitleStreamsList = []
        subtitleStreams = ['1 No subtitles']
        downloadableStreams = []
        # selectAudioIndex = ""
        selectSubsIndex = ""
        playurlprefs = {}

        # Set part where we're at
        self.API.setPartNumber(part)
        if part is None:
            part = 0
        try:
            mediastreams = self.item[0][part]
        except (TypeError, KeyError, IndexError):
            return url

        audioNum = 0
        # Remember 'no subtitles'
        subNum = 1
        for stream in mediastreams:
            # Since Emby returns all possible tracks together, have to sort them.
            index = stream.attrib.get('id')
            type = stream.attrib.get('streamType')

            # Audio
            if type == "2":
                codec = stream.attrib.get('codec')
                channelLayout = stream.attrib.get('audioChannelLayout', "")
               
                try:
                    track = "%s %s - %s %s" % (audioNum+1, stream.attrib['language'], codec, channelLayout)
                except:
                    track = "%s 'unknown' - %s %s" % (audioNum+1, codec, channelLayout)
                
                #audioStreamsChannelsList[audioNum] = stream.attrib['channels']
                audioStreamsList.append(index)
                audioStreams.append(utils.tryEncode(track))
                audioNum += 1

            # Subtitles
            elif type == "3":
                '''if stream['IsExternal']:
                    continue'''
                try:
                    track = "%s %s" % (subNum+1, stream.attrib['language'])
                except:
                    track = "%s 'unknown' (%s)" % (subNum+1, stream.attrib.get('codec'))

                default = stream.attrib.get('default')
                forced = stream.attrib.get('forced')
                downloadable = stream.attrib.get('key')

                if default:
                    track = "%s - Default" % track
                if forced:
                    track = "%s - Forced" % track
                if downloadable:
                    downloadableStreams.append(index)

                subtitleStreamsList.append(index)
                subtitleStreams.append(utils.tryEncode(track))
                subNum += 1

        if audioNum > 1:
            resp = dialog.select(lang(33013), audioStreams)
            if resp > -1:
                # User selected audio
                playurlprefs['audioStreamID'] = audioStreamsList[resp]
            else: # User backed out of selection - let PMS decide
                pass
        else: # There's only one audiotrack.
            playurlprefs['audioStreamID'] = audioStreamsList[0]

        # Add audio boost
        playurlprefs['audioBoost'] = utils.settings('audioBoost')

        if subNum > 1:
            resp = dialog.select(lang(33014), subtitleStreams)
            if resp == 0:
                # User selected no subtitles
                playurlprefs["skipSubtitles"] = 1
            elif resp > -1:
                # User selected subtitles
                selectSubsIndex = subtitleStreamsList[resp-1]

                # Load subtitles in the listitem if downloadable
                if selectSubsIndex in downloadableStreams:

                    url = "%s/library/streams/%s" \
                          % (self.server, selectSubsIndex)
                    url = self.API.addPlexHeadersToUrl(url)
                    self.logMsg("Downloadable sub: %s: %s" % (selectSubsIndex, url), 1)
                    listitem.setSubtitles([utils.tryEncode(url)])
                else:
                    self.logMsg('Need to burn in subtitle %s' % selectSubsIndex, 1)
                    playurlprefs["subtitleStreamID"] = selectSubsIndex
                    playurlprefs["subtitleSize"] = utils.settings('subtitleSize')

            else: # User backed out of selection
                pass

        # Tell the PMS what we want with a PUT request
        # url = self.server + '/library/parts/' + self.item[0][part].attrib['id']
        # PlexFunctions.SelectStreams(url, playurlprefs)
        url += '&' + urlencode(playurlprefs)

        # Get number of channels for selected audio track
        # audioChannels = audioStreamsChannelsList.get(selectAudioIndex, 0)
        # if audioChannels > 2:
        #     playurlprefs += "&AudioBitrate=384000"
        # else:
        #     playurlprefs += "&AudioBitrate=192000"

        return url
