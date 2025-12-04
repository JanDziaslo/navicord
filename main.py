import config
import requests
import time
import threading
import json
import os
import re

from rpc import DiscordRPC


class PersistentStore:
    data = {}
    has_loaded = False
    lock = threading.RLock()
    filename = os.path.join(os.path.dirname(__file__), "images.json")

    @classmethod
    def get(cls, key):
        if not cls.has_loaded:
            cls.load()

        return cls.data.get(key)

    @classmethod
    def set(cls, key, value):
        if not cls.has_loaded:
            cls.load()

        with cls.lock:
            cls.data[key] = value
            cls.save()

    @classmethod
    def has(cls, key):
        if not cls.has_loaded:
            cls.load()

        return key in cls.data

    @classmethod
    def load(cls):
        try:
            with open(cls.filename) as file:
                cls.data = json.load(file)
        except FileNotFoundError:
            cls.data = {}

        cls.has_loaded = True

    @classmethod
    def save(cls):
        with cls.lock:
            with open(cls.filename + ".tmp", "w") as file:
                file.write(json.dumps(cls.data))

            os.replace(cls.filename + ".tmp", cls.filename)


class CurrentTrack:
    id = None
    album_id = None

    title = None
    artist = None
    album = None

    started_at = None
    ends_at = None

    image_url = None

    def _filter_nowplaying(entry):
        return [
            player
            for player in entry
            if player["username"] == config.NAVIDROME_USERNAME
        ]

    @classmethod
    def set(cls, skip_none_check=False, **kwargs):
        if "image_url" in kwargs:
            cls.image_url = kwargs.get("image_url")

        id = kwargs.get("id")
        duration = kwargs.get("duration")
        artist = kwargs.get("artist")
        album = kwargs.get("album")
        title = kwargs.get("title")
        album_id = kwargs.get("album_id")

        if (
            None in [id, duration, artist, album, title, album_id]
            and not skip_none_check
        ):
            return

        if id == cls.id:
            return

        cls.id = id
        cls.album_id = album_id
        cls.title = title
        cls.artist = artist
        cls.album = album
        cls.image_url = None  # Reset image when track changes
        cls.started_at = time.time()
        cls.ends_at = cls.started_at + (duration or 0)

    @classmethod
    def _grab_subsonic(cls):
        res = requests.get(
            f"{config.NAVIDROME_SERVER}/rest/getNowPlaying",
            params={
                "u": config.NAVIDROME_USERNAME,
                "p": config.NAVIDROME_PASSWORD,
                "f": "json",
                "v": "1.13.0",
                "c": "navicord",
            },
        )

        if res.status_code != 200:
            print("There was an error getting now playing: ", res.text)
            return

        try:
            json = res.json()["subsonic-response"]
        except:
            print("There was an error parsing subsonic response: ", res.text)
            return

        if len(json["nowPlaying"]) == 0:
            cls.set(skip_none_check=True)
            return

        if json["status"] == "ok" and len(json["nowPlaying"]) > 0:
            nowPlayingEntry = json["nowPlaying"]["entry"]
            nowPlayingList = cls._filter_nowplaying(nowPlayingEntry)

            if len(nowPlayingList) == 0:
                cls.set(skip_none_check=True)
                return

            nowPlaying = nowPlayingList[0]

            cls.set(
                id=nowPlaying["id"],
                duration=nowPlaying["duration"],
                artist=nowPlaying["artist"],
                album=nowPlaying["album"],
                title=nowPlaying["title"],
                album_id=nowPlaying["albumId"],
            )

    @classmethod
    def _split_artists(cls, artist_string):
        separators = r'\s*/\s*|\s*&\s*|\s*,\s*|\s+feat\.?\s+|\s+ft\.?\s+|\s+featuring\s+|\s+and\s+|\s*;\s*'
        artists = re.split(separators, artist_string, flags=re.IGNORECASE)
        artists = [a.strip() for a in artists if a.strip()]
        return artists

    @classmethod
    def _try_get_lastfm_image(cls, artist, album):
        try:
            res = requests.get(
                "https://ws.audioscrobbler.com/2.0/",
                params={
                    "method": "album.getinfo",
                    "api_key": config.LASTFM_API_KEY,
                    "artist": artist,
                    "album": album,
                    "format": "json",
                },
            )

            if res.status_code == 200:
                data = res.json()
                if "album" in data and "image" in data["album"]:
                    image_url = data["album"]["image"][3]["#text"]
                    if image_url:
                        return image_url
        except Exception:
            pass
        return None

    @classmethod
    def _grab_lastfm(cls):
        if PersistentStore.has(cls.album_id):
            cached_url = PersistentStore.get(cls.album_id)
            cls.set(image_url=cached_url if cached_url else None)
            return

        image_url = cls._try_get_lastfm_image(cls.artist, cls.album)

        if not image_url:
            artists = cls._split_artists(cls.artist)
            for artist in artists:
                if artist != cls.artist:
                    image_url = cls._try_get_lastfm_image(artist, cls.album)
                    if image_url:
                        break

        if not image_url:
            PersistentStore.set(cls.album_id, "")
            cls.set(image_url=None)
            return

        cls.set(image_url=image_url)
        PersistentStore.set(cls.album_id, image_url)

    @classmethod
    def grab(cls):
        cls._grab_subsonic()

        if cls.artist and cls.album:
            cls._grab_lastfm()


rpc = DiscordRPC(config.DISCORD_CLIENT_ID, config.DISCORD_TOKEN)

time_passed = 5

while True:
    time.sleep(config.POLLING_TIME)

    CurrentTrack.grab()

    if time_passed >= 5:
        time_passed = 0

        if CurrentTrack.id is None:
            rpc.clear_activity()
            continue

        match config.ACTIVITY_NAME:
            case "ARTIST":
                activity_name = CurrentTrack.artist
            case "ALBUM":
                activity_name = CurrentTrack.album
            case "TRACK":
                activity_name = CurrentTrack.title
            case _:
                activity_name = config.ACTIVITY_NAME

        rpc.send_activity(
            {
                "application_id": config.DISCORD_CLIENT_ID,
                "type": 2,
                "state": CurrentTrack.album,
                "details": CurrentTrack.title,
                "assets": {
                    "large_image": CurrentTrack.image_url,
                },
                "timestamps": {
                    "start": CurrentTrack.started_at * 1000,
                    "end": CurrentTrack.ends_at * 1000,
                },
                "name": activity_name,
            }
        )

    time_passed += 1
