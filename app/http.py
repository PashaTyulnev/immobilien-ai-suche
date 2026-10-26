"""HTTP-Zugriff mit Browser-Fingerprint, Drosselung und Wiederholungen."""
import random
import threading
import time

from curl_cffi import requests


class Fetcher:
    """Ein Fetcher pro Quelle: hält Session/Cookies und drosselt Anfragen an denselben Host."""

    def __init__(self, delay: float = 1.5):
        self.delay = delay
        self.session = requests.Session(impersonate="chrome")
        self._last = 0.0
        self._lock = threading.Lock()

    def reset(self):
        """Neue Session (frische Cookies) – manche Portale liefern nach vielen Anfragen abgespeckte Seiten."""
        self.session = requests.Session(impersonate="chrome")

    def get(self, url: str, retries: int = 2) -> requests.Response:
        last_exc = None
        for attempt in range(retries + 1):
            with self._lock:
                wait = self._last + self.delay + random.uniform(0, self.delay / 2) - time.time()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
            try:
                r = self.session.get(url, timeout=30, headers={"Accept-Language": "de-DE,de;q=0.9"})
                if r.status_code in (429, 503) and attempt < retries:
                    time.sleep(10 * (attempt + 1))
                    continue
                return r
            except Exception as e:  # Netzwerkfehler -> nochmal versuchen
                last_exc = e
                time.sleep(3 * (attempt + 1))
        raise last_exc
