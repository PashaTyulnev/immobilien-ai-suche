"""ImmoScout24 über Suchauftrag-E-Mails (die Website selbst blockt automatische Abrufe).

Liest per IMAP die Benachrichtigungen von ImmoScout24 aus einem Postfach und zieht pro Inserat
Titel, Text, Bild und Link heraus. Preise/Fläche/Zimmer holt danach die normale Text-Extraktion bzw. das LLM.
"""
import email
import imaplib
import re
import time
from email.header import decode_header, make_header
from urllib.parse import unquote

from bs4 import BeautifulSoup
from curl_cffi import requests

from ..config import load_config
from ..extract import num
from .base import Scraper, clean

# direkt oder über den Mail-Tracker push.search.is24.de/email/expose/<id>
EXPOSE = re.compile(r"(?:immobilienscout24|is24)\.de/(?:email/)?expose/(\d{6,12})")


def _unquote_all(s: str) -> str:
    for _ in range(3):  # Tracking-Links sind teils mehrfach kodiert
        s = unquote(s)
    return s


def expose_id(href: str) -> str | None:
    m = EXPOSE.search(_unquote_all(href or ""))
    return m.group(1) if m else None


def parse_mail(html: str) -> list[dict]:
    """Ein Inserat pro Expose-ID: kleinster Block um den Link, der nur dieses Inserat enthält."""
    s = BeautifulSoup(html, "lxml")
    # Der Preis in der Mail folgt dem Kriterium des Suchauftrags ("bis 1.400 € Warmmiete" -> Warmmiete)
    price_key = "price_warm" if "Warmmiete" in s.get_text(" ") else "price_cold"
    out = {}
    for a in s.select("a[href]"):
        eid = expose_id(a["href"])
        if not eid or eid in out:
            continue
        block = a
        while block.parent is not None and block.parent.name not in ("body", "[document]"):
            links = block.parent.select("a[href]")
            ids = {expose_id(x["href"]) for x in links} - {None}
            # Stopp, sobald ein anderes Inserat oder Kopf/Fuß der Mail ("Alle Angebote") mit drin wäre
            if ids - {eid} or any("executeSavedSearch" in x["href"] or "savedsearch" in x["href"] for x in links):
                break
            block = block.parent
        text = clean(block.get_text("\n"))
        lines = [l.strip(" *") for l in text.split("\n") if l.strip(" *")]
        # Aufbau je Inserat: Titel / Preis / Fläche / Zimmer / "Straße, Stadtteil, Dresden" / Anbieter
        item = {"ext_id": eid, "url": f"https://www.immobilienscout24.de/expose/{eid}",
                "title": (lines[0] if lines else "")[:200], "description": text}
        for i, l in enumerate(lines):
            if re.fullmatch(r"[\d.]+(?:,\d+)?\s*€", l):
                item[price_key] = num(l)
            elif re.fullmatch(r"[\d.,]+\s*m²", l):
                item["area"] = num(l)
            elif re.fullmatch(r"[\d,]+\s*Zi\.?", l):
                item["rooms"] = num(l)
            elif "area" in item and "," in l and "address" not in item:
                # Ortszeile nach den Eckdaten: "[Straße, ]Stadtteil, Stadt"
                item["address"] = l
                parts = [p.strip() for p in l.split(",")]
                item["district"] = parts[-2] if len(parts) >= 2 else None
                item["city"] = parts[-1]
                if i + 1 < len(lines):
                    item["provider"] = lines[i + 1]
        if not (item.get("city") or "").startswith("Dresden"):
            continue  # z.B. alte Suchaufträge für andere Städte im Postfach
        item.pop("city")
        # nur Wohnungsfotos, nicht die Anbieter-Logos (usercontent)
        item["images"] = [re.sub(r"/ORIG/.*$", "/ORIG/resize/1200x900%3E/format/jpg/quality/80", i["src"])  # statt Mini-Vorschau
                          for i in block.select("img[src]") if "pictures.immobilienscout24.de/listings/" in i["src"]] or None
        out[eid] = item
    return list(out.values())


def photo_alive(item: dict) -> bool:
    """False, wenn ImmoScout das Foto gelöscht hat (404). Ohne Foto oder bei Netzfehlern: im Zweifel behalten."""
    if not item.get("images"):
        return True
    try:
        r = requests.head(item["images"][0], impersonate="chrome", timeout=15)
        return r.status_code != 404
    except Exception:
        return True


def _decode(value) -> str:
    return str(make_header(decode_header(value or "")))


class ImmoScoutMail(Scraper):
    name = "immoscout"
    label = "ImmoScout24 (Mail)"
    tracks_removal = True  # Inserate mit gelöschtem Foto bzw. älter als "days" gelten als offline

    def search(self, page):
        if page > 1:
            return []
        cfg = load_config()["sources"]["immoscout"]
        if not cfg.get("app_password"):
            raise RuntimeError("Kein App-Passwort in config.json (sources.immoscout.app_password)")
        imap = imaplib.IMAP4_SSL(cfg.get("imap_host", "imap.gmail.com"))
        try:
            imap.login(cfg["user"], cfg["app_password"])
            # "Alle Nachrichten"-Ordner (Name ist sprachabhängig, erkennbar am \All-Flag), sonst INBOX
            _, boxes = imap.list()
            box = next((re.search(r'"([^"]+)"\s*$', b.decode()).group(1) for b in boxes or [] if b and b"\\All" in b), "INBOX")
            typ, _ = imap.select(f'"{box}"', readonly=True)
            if typ != "OK":
                raise RuntimeError(f"Postfach-Ordner {box} nicht lesbar")
            since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - cfg.get("days", 14) * 86400))
            _, data = imap.search(None, f'(FROM "immobilienscout24" SINCE {since})')
            items = {}
            for num in data[0].split():
                _, msg_data = imap.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                html = next((p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace")
                             for p in msg.walk() if p.get_content_type() == "text/html"), None)
                if not html:
                    continue
                sent = email.utils.parsedate_to_datetime(msg["Date"]).isoformat() if msg["Date"] else None
                for it in parse_mail(html):
                    it["published"] = sent
                    items[it["ext_id"]] = it  # Mails kommen chronologisch -> neueste Fassung (z.B. Preisänderung) gewinnt
            # ImmoScout löscht die Fotos, sobald ein Inserat offline ist -> Foto weg = Wohnung weg.
            # Nicht mehr zurückgegebene Inserate markiert die Pipeline als offline (tracks_removal).
            return [it for it in items.values() if photo_alive(it)]
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def detail(self, item):
        return {}
