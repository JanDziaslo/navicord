import config
import requests
import time
import urllib.parse
import json
import os
import threading
import signal

from rpc import DiscordRPC


# Uguu.se expiry time - 3 hours in seconds
UGUU_EXPIRY_SECONDS = 3 * 60 * 60


class PersistentStore:
    data = {}
    has_loaded = False
    lock = threading.RLock()
    filename = os.path.join(os.path.dirname(__file__), "images.json")

    @classmethod
    def get(cls, key):
        if not cls.has_loaded:
            cls.load()

        entry = cls.data.get(key)
        if entry is None:
            return None

        # Check if entry has expiration info (new format)
        if isinstance(entry, dict) and "url" in entry and "created_at" in entry:
            # Check if image has expired (3 hours for uguu.se)
            if time.time() - entry["created_at"] >= UGUU_EXPIRY_SECONDS:
                # Image expired, remove it
                cls.delete(key)
                return None
            return entry["url"]

        # Legacy format (just URL string) - delete and return None to force re-upload
        cls.delete(key)
        return None

    @classmethod
    def set(cls, key, value):
        if not cls.has_loaded:
            cls.load()

        with cls.lock:
            cls.data[key] = {
                "url": value,
                "created_at": time.time()
            }
            cls.save()

    @classmethod
    def delete(cls, key):
        if not cls.has_loaded:
            cls.load()

        with cls.lock:
            if key in cls.data:
                del cls.data[key]
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
        except (FileNotFoundError, json.JSONDecodeError):
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

    @staticmethod
    def _filter_nowplaying(entry):
        if isinstance(entry, dict):
            entry = [entry]
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
            return False

        if not skip_none_check and id == cls.id:
            return False

        cls.id = id
        cls.album_id = album_id
        cls.title = title
        cls.artist = artist
        cls.album = album
        cls.image_url = kwargs.get("image_url")
        cls.started_at = time.time()
        cls.ends_at = cls.started_at + (duration or 0)
        return True

    @classmethod
    def _upload_to_0x0(cls, image_content):
        try:
            files = {'file': ('cover.jpg', image_content)}
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            res = requests.post("https://0x0.st", files=files, headers=headers)
            if res.status_code == 200:
                return res.text.strip()
            print(f"0x0.st upload failed: {res.status_code} - {res.text}")
        except Exception as e:
            print(f"0x0.st upload exception: {e}")
        return None

    @classmethod
    def _upload_to_uguu(cls, image_content):
        try:
            files = {'files[]': ('cover.jpg', image_content)}
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            res = requests.post("https://uguu.se/upload", files=files, headers=headers)
            if res.status_code == 200:
                data = res.json()
                if data.get("success"):
                    return data["files"][0]["url"]
            print(f"uguu.se upload failed: {res.status_code} - {res.text}")
        except Exception as e:
            print(f"uguu.se upload exception: {e}")
        return None

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
            return cls.set(
                skip_none_check=True,
                id=None,
                duration=None,
                artist=None,
                album=None,
                title=None,
                album_id=None,
                image_url=None,
            )

        if json["status"] == "ok" and len(json["nowPlaying"]) > 0:
            nowPlayingEntry = json["nowPlaying"]["entry"]
            nowPlayingList = cls._filter_nowplaying(nowPlayingEntry)

            if len(nowPlayingList) == 0:
                cls.set(skip_none_check=True)
                return

            nowPlaying = nowPlayingList[0]
            album_id = nowPlaying["albumId"]

            image_url = None
            if PersistentStore.has(album_id):
                cached = PersistentStore.get(album_id)
                if cached:
                    image_url = cached

            if not image_url and "coverArt" in nowPlaying:
                params = {
                    "id": nowPlaying['coverArt'],
                    "u": config.NAVIDROME_USERNAME,
                    "p": config.NAVIDROME_PASSWORD,
                    "v": "1.13.0",
                    "c": "navicord"
                }
                query_string = urllib.parse.urlencode(params)
                navidrome_url = f"{config.NAVIDROME_SERVER}/rest/getCoverArt?{query_string}"

                try:
                    img_res = requests.get(navidrome_url)
                    if img_res.status_code == 200:
                        uploaded_url = cls._upload_to_0x0(img_res.content)
                        if not uploaded_url:
                            uploaded_url = cls._upload_to_uguu(img_res.content)

                        if uploaded_url:
                            image_url = uploaded_url
                            PersistentStore.set(album_id, image_url)
                        else:
                            print("Failed to upload to 0x0.st and uguu.se")
                            # Fallback to navidrome URL if upload fails, though it might not work if local
                            image_url = navidrome_url
                    else:
                        print(f"Failed to fetch cover from Navidrome: {img_res.status_code}")
                except Exception as e:
                    print(f"Error processing cover: {e}")
                    image_url = navidrome_url

            cls.set(
                id=nowPlaying["id"],
                duration=nowPlaying["duration"],
                artist=nowPlaying["artist"],
                album=nowPlaying["album"],
                title=nowPlaying["title"],
                album_id=nowPlaying["albumId"],
                image_url=image_url,
            )

    @classmethod
    def grab(cls):
        return cls._grab_subsonic()



rpc = DiscordRPC(config.DISCORD_CLIENT_ID, config.DISCORD_TOKEN)


# Treat container PID1 exit the same as SIGTERM/SIGINT to clear presence quickly

def _graceful_shutdown(signum=None, frame=None):
    try:
        rpc.shutdown()
    finally:
        raise SystemExit(0)

# Ensure Discord presence is cleared on exit signals
signal.signal(signal.SIGINT, _graceful_shutdown)
signal.signal(signal.SIGTERM, _graceful_shutdown)

# If running as PID 1 (common in Docker), also catch SIGINT/SIGTERM already set; nothing extra needed

time_passed = 5

while True:
    time.sleep(config.POLLING_TIME)

    changed = CurrentTrack.grab()

    if changed:
        if CurrentTrack.id is None:
            rpc.clear_activity()
            time_passed = 0
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
        time_passed = 0
        continue

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
