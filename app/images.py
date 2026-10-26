"""Bild-Cache auf der Festplatte: Bilder bleiben erhalten, auch wenn Links ablaufen oder Inserate offline gehen."""
import hashlib
from urllib.parse import urlparse

from curl_cffi import requests

from .config import DATA_DIR

CACHE = DATA_DIR / "img_cache"
CACHE.mkdir(exist_ok=True)
HOSTS = ("mms.immowelt.de", "img.kleinanzeigen.de", "media.ohne-makler.net", "www.ohne-makler.net",
         "img.wg-gesucht.de", "immobilien-api.fly.dev", "pictures.immobilienscout24.de", "www.dresdden.de")
# Quellen mit ablaufenden (signierten) Bild-Links -> komplette Galerie sofort sichern
CACHE_ALL = {"ohnemakler"}


def allowed(url: str) -> bool:
    return urlparse(url).hostname in HOSTS


def get(url: str) -> tuple[bytes, str] | None:
    key = hashlib.sha1(url.encode()).hexdigest()
    path, tpath = CACHE / key, CACHE / (key + ".type")
    if not path.exists():
        r = requests.get(url, impersonate="chrome", timeout=20)
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or not ctype.startswith("image"):
            return None
        path.write_bytes(r.content)
        tpath.write_text(ctype)
    return path.read_bytes(), tpath.read_text() if tpath.exists() else "image/jpeg"


def prefetch(source: str, urls: list[str] | None):
    for u in (urls or [])[: None if source in CACHE_ALL else 1]:
        try:
            get(u)
        except Exception:
            pass
