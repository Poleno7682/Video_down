# yt-dlp plugin extractor for rezka.ag / hdrezka.* and mirrors.
#
# Vendored (not pip-installed) from https://github.com/gnfalex/rezka_yt_dlp_plugin
# (Unlicense) so it ships inside the worker image without an extra runtime
# dependency. Modified for unattended use: the upstream version blocks on
# input() to ask which translator/voiceover to use whenever a page offers
# more than one and none was pre-selected via extractor-args — that would
# hang a Celery task forever (no timeout catches blocking stdin reads).
# This picks the first available translator automatically instead. Also
# dropped a debug code path that wrote raw decode failures to timestamped
# .txt files in the process's cwd.
#
# ⚠ Don't use relative imports
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils.traversal import traverse_obj
import re, json
import os, base64
import time
import urllib.parse

from yt_dlp.utils import (
    get_elements_by_attribute,
    get_elements_html_by_attribute,
    extract_attributes,
)


def split_rezka(inStr):
    if not inStr:
        return []
    inStr = re.sub(r'<.*?prem-quality.*?">', 'PremuimStub-', inStr)
    inStr = re.sub(r'<.*?>', '', inStr)
    result = []
    for entry in inStr.split(","):
        idx = entry.index("]")
        for url_data in entry[idx + 1:].split(" or "):
            result.append({
                "name": entry[1:idx],
                "url": url_data,
                "ext": os.path.splitext(url_data)[1][1:],
            })
    return result


def decode_rezka(inStr):
    if not inStr:
        return []
    if "http" in inStr:
        return split_rezka(inStr)
    bk = [
        "$$#!!@#!@##",
        "^^^!@##!!##",
        "####^!!##!@@",
        "@@@@@!##!^^^",
        "$$!!@$$@^!@#$$@",
        "####^!!##!@@",
    ]
    fs = "//_//"
    tmpStr = inStr[2:]
    for bx in bk:
        tmpStr = tmpStr.replace(fs + base64.b64encode(bx.encode()).decode(), "")
    tmpStr = base64.b64decode(tmpStr).decode()
    return split_rezka(tmpStr)


def parse_episodes(inStr):
    result = {}
    episodesStr = inStr.replace(" active", "")
    for episode in get_elements_html_by_attribute("class", "b-simple_episode__item", episodesStr):
        attrs = extract_attributes(episode)
        s_id = attrs.get("data-season_id", "0")
        e_id = attrs.get("data-episode_id", "0")
        if s_id not in result:
            result[s_id] = []
        result[s_id].append(e_id)
    return result


def rezka_dict(info, urlp):
    origin = urlp.scheme + "://" + urlp.hostname
    referer = origin + "/"

    result = {}
    _FORMATS = {
        "360p": {"w": 360, "h": 240},
        "480p": {"w": 480, "h": 360},
        "720p": {"w": 720, "h": 480},
        "1080p": {"w": 1080, "h": 720},
        "1080p Ultra": {"w": 2160, "h": 1440},
    }
    formats = []
    subtitles = {}
    for format_data in (decode_rezka(info.get("streams")) + decode_rezka(info.get("url"))):
        formats.append({
            "format": format_data.get("name"),
            "format_id": format_data.get("name"),
            "format_note": urllib.parse.urlparse(format_data.get("url")).hostname,
            "url": format_data.get("url"),
            "ext": "mp4",
            "container": format_data.get("ext"),
            "width": traverse_obj(_FORMATS, (format_data.get("name"), "w"), 0),
            "height": traverse_obj(_FORMATS, (format_data.get("name"), "h"), 0),
            "preference": -99 if "Premuim" in format_data.get("name") else -2 if format_data.get("ext") == "m3u8" else -1,
            "headers": {
                "Origin": origin,
                "Referer": referer,
            },
        })
    for sub_data in split_rezka(info.get("subtitle")):
        sub_code = traverse_obj(info, ("subtitle_lns", sub_data.get("name")), "zz")
        if sub_code not in subtitles:
            subtitles[sub_code] = []
        subtitles[sub_code].append(sub_data)
    if formats:
        result["formats"] = formats
    if subtitles:
        result["subtitles"] = subtitles
    return result


class RezkaIE(InfoExtractor):
    _WORKING = True
    _VALID_URL = r'^https?://h?d?rezka(?:-ua)?\..*/(?P<id>\d+)-(?P<name>[^/]+)-(?P<year>\d+)(?P<other>-.*)?\.html.*'
    _SCRIPT_REGEX = r'initCDN(Movies|Series)Events\(([^;]*})\);'
    _DICT_HEADERS = ["id", "translator_id", "camrip", "ads", "director", "domain", "unknown1", "unknown2", "info"]
    _DOMAIN = ""

    def call_rezkaAPI(self, domain="", data="", action=""):
        postdata = {x: int(data.get(x, 0)) for x in ["id", "translator_id"]}
        postdata.update({"is_" + x: int(data.get(x, 0)) for x in ["camrip", "ads", "director"]})
        postdata.update({"action": action})
        url = f'https://{domain}/ajax/get_cdn_series/?t={str(int(1000 * (time.time())))}'
        vid = f'{postdata.get("id", 0)}_{postdata.get("translator_id", 0)}'
        if "season" in data:
            postdata.update({x: data[x] for x in ["season", "episode"]})
            vid = vid + f'_{data["season"]}_{data["episode"]}'
        time.sleep(1)
        return self._download_json(
            url,
            video_id=vid,
            data=urllib.parse.urlencode(postdata).encode("utf8"),
        )

    def _real_extract(self, url):
        video_id = self._match_id(url)
        webpage = self._download_webpage(url, video_id)
        video_title = " _ ".join(
            re.sub(r'\s*<[^>]*>\s*', '', x)
            for x in get_elements_by_attribute("class", "b-post__title", webpage, tag="div")
        )
        video_alttitle = " _ ".join(get_elements_by_attribute("class", "b-post__origtitle", webpage, tag="div"))
        video_alttitle = video_alttitle if video_alttitle else video_title

        translationList = (
            get_elements_html_by_attribute("class", "b-translator__item active", webpage, tag="li")
            + get_elements_html_by_attribute("class", "b-translator__item", webpage, tag="li")
            + get_elements_html_by_attribute("class", "b-translator__item active", webpage, tag="a")
            + get_elements_html_by_attribute("class", "b-translator__item", webpage, tag="a")
        )
        scriptData = re.compile(self._SCRIPT_REGEX).search(webpage)
        if not scriptData:
            self.report_error("Cant find scriptData")
            return {}
        video_type = scriptData.group(1)
        scriptTxt = "[" + re.sub(r", '([^']*)',", r', "\1",', scriptData.group(2)) + "]"
        scriptData = dict(zip(self._DICT_HEADERS, json.loads(scriptTxt)))
        urlp = urllib.parse.urlparse(url)
        self._DOMAIN = str(scriptData.get("domain", urlp.hostname))

        if not translationList:
            return {
                "_type": "video",
                "id": video_id,
                "title": video_title,
                "alt_title": video_alttitle,
                **rezka_dict(scriptData["info"], urlp),
            }

        trDict = {}
        # extractor-args rezka:translator=<id> pins a specific voiceover;
        # otherwise the first translator on the page is used automatically
        # (unattended — never blocks waiting for interactive input).
        requested_translator = self._configuration_arg('translator', [None])[0]
        for tr in translationList:
            trInfo = {key.replace("data-", ""): val for key, val in extract_attributes(tr).items()}
            if requested_translator is not None and requested_translator != trInfo.get("translator_id"):
                continue
            if "id" not in trInfo:
                trInfo["id"] = video_id
            trDict[trInfo["translator_id"]] = trInfo
            if video_type == "Series":
                try:
                    json_resp = self.call_rezkaAPI(domain=self._DOMAIN, data=trInfo, action="get_episodes")
                    trInfo["episodes"] = parse_episodes(json_resp["episodes"])
                except Exception:
                    self.report_warning(f"Could not fetch episode list for translator {trInfo.get('translator_id')}")

        if not trDict:
            self.report_error("No matching translator found")
            return {}

        translator_id = requested_translator if requested_translator in trDict else next(iter(trDict))
        selected = trDict[translator_id]

        if video_type == "Series":
            out = {
                "_type": "playlist",
                "id": video_id,
                "title": video_title,
                "alt_title": video_alttitle,
                "entries": [],
            }
            for season, episodes in selected.get("episodes", {}).items():
                for episode in episodes:
                    try:
                        json_resp = self.call_rezkaAPI(
                            domain=self._DOMAIN,
                            data={**selected, "season": season, "episode": episode},
                            action="get_stream",
                        )
                        out["entries"].append({
                            "_type": "video",
                            "id": video_id,
                            "title": f"s{int(season):02d}e{int(episode):02d} {video_title}",
                            "alt_title": video_alttitle,
                            "season": season,
                            "episode": episode,
                            **rezka_dict(json_resp, urlp),
                        })
                    except Exception:
                        self.report_warning(f"Could not fetch stream for s{season}e{episode}")
            return out

        json_resp = self.call_rezkaAPI(domain=self._DOMAIN, data=selected, action="get_movie")
        return {
            "_type": "video",
            "id": video_id,
            "title": video_title,
            "alt_title": video_alttitle,
            **rezka_dict(json_resp, urlp),
        }
