import json
import time
import logging
import threading
import websocket
import requests


class DiscordRPC:
    def __init__(self, app_id, token):
        self.app_id = app_id
        self.token = token
        self.ws = None
        self.seq = None
        self._stopped = threading.Event()
        self.uri = "wss://gateway.discord.gg/?encoding=json&v=9"
        threading.Thread(target=self._connect, daemon=True).start()

    def _connect(self):
        while not self._stopped.is_set():
            time.sleep(5)
            if self.ws:
                continue
            try:
                discord_gateway_url = requests.get(
                    "https://discord.com/api/gateway"
                ).json()["url"]
                self.ws = websocket.WebSocketApp(
                    discord_gateway_url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                )
                threading.Thread(target=self.ws.run_forever, daemon=True).start()
                self._ping_loop()
            except Exception as e:
                logging.error(f"WebSocket connection error: {e}")

    def _on_message(self, ws, message):
        data = json.loads(message)
        if "s" in data:
            self.seq = data["s"]

    def _ping_loop(self):
        while self.ws and not self._stopped.is_set():
            time.sleep(41.25)
            try:
                self.ws.send(json.dumps({"op": 1, "d": self.seq}))
            except Exception:
                self._connect()

    def _on_open(self, ws):
        logging.info("WebSocket to Discord gateway opened")
        self.ws.send(
            json.dumps(
                {
                    "op": 2,
                    "d": {
                        "token": self.token,
                        "intents": 0,
                        "properties": {
                            "os": "Windows 10",
                            "browser": "Discord Client",
                            "device": "Discord Client",
                        },
                    },
                }
            )
        )

    def _on_error(self, ws, error):
        logging.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status, close_msg):
        self.ws = None
        logging.info(f"WebSocket closed: {close_msg}")

    def _process_image(self, image_url):
        if image_url is None:
            return self._process_image("https://i.imgur.com/hb3XPzA.png")

        if image_url.startswith("mp:"):
            return image_url

        url = f"https://discord.com/api/v9/applications/{self.app_id}/external-assets"
        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": self.token,
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
                json={"urls": [image_url]},
            )

            if response.status_code != 200:
                print(f"Discord external-assets error: {response.status_code} - {response.text}")
                # Try returning the URL directly as a fallback, sometimes works for certain clients/activities
                return image_url

            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                image = data[0]["external_asset_path"]
                return f"mp:{image}"
            else:
                print(f"Discord external-assets unexpected response: {data}")

        except Exception as e:
            print(f"Discord external-assets exception: {e}")

        # Fallback to default image if everything fails
        # return self._process_image("https://i.imgur.com/hb3XPzA.png")
        # Actually, let's return the original URL if we can't proxy it, maybe the client can handle it
        return image_url

    def send_activity(self, activity_data):
        if not self.ws or self._stopped.is_set():
            return

        activity_data["assets"]["large_image"] = self._process_image(
            activity_data["assets"]["large_image"]
        )

        payload = {
            "op": 3,
            "d": {
                "since": None,
                "activities": [activity_data],
                "status": "dnd",
                "afk": False,
            },
        }
        try:
            self.ws.send(json.dumps(payload))
        except Exception:
            self._connect()

    def clear_activity(self):
        if not self.ws or self._stopped.is_set():
            return
        payload = {
            "op": 3,
            "d": {
                "since": None,
                "activities": [None],
                "status": None,
                "afk": None,
            },
        }
        try:
            self.ws.send(json.dumps(payload))
        except Exception:
            self._connect()

    def shutdown(self):
        """Clear presence and close the websocket so Discord clears immediately."""
        self._stopped.set()
        try:
            self.clear_activity()
        finally:
            if self.ws:
                try:
                    self.ws.close()
                except Exception:
                    pass
                self.ws = None
