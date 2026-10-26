"""wg-gesucht.de – Wohnungen und 1-Zimmer-Wohnungen in Dresden (oft befristet)."""
import re

from bs4 import BeautifulSoup

from ..extract import num
from .base import Scraper, check_blocked, clean, node_text

BASE = "https://www.wg-gesucht.de"
LISTS = ["/wohnungen-in-Dresden.27.2.1.{p}.html", "/1-zimmer-wohnungen-in-Dresden.27.1.1.{p}.html"]
AD_LINK = re.compile(r"^/(?:1-zimmer-)?wohnungen-in-Dresden(?:-([^.]+))?\.(\d{5,})\.html$")


class WgGesucht(Scraper):
    name = "wggesucht"
    label = "WG-Gesucht"

    def search(self, page):
        out, seen = [], set()
        for path in LISTS:
            s = self.soup(BASE + path.format(p=page - 1))
            for a in s.select("a[href]"):
                m = AD_LINK.match(a["href"])
                if not m or m.group(2) in seen:
                    continue
                seen.add(m.group(2))
                item = {"ext_id": m.group(2), "url": BASE + a["href"],
                        "district": (m.group(1) or "").replace("-", " ") or None}
                # Karte um den Link: "726 € pro Monat" (Gesamtmiete) · "62 m²" · "3-Zimmer-Wohnung"
                card = a
                for _ in range(8):
                    if card.parent is None or len({x["href"] for x in card.parent.select("a[href]") if AD_LINK.match(x["href"])}) > 1:
                        break
                    card = card.parent
                t = " ".join(card.get_text(" ").split())
                if p := re.search(r"(\d[\d.]*)\s*€", t):
                    item["price_warm"] = num(p.group(1))
                if ar := re.search(r"(\d+(?:,\d+)?)\s*m²", t):
                    item["area"] = num(ar.group(1))
                if r := re.search(r"(\d(?:,5)?)-Zimmer-Wohnung", t):
                    item["rooms"] = num(r.group(1))
                item["title"] = node_text(card.select_one("h3, .truncate_title")) or None
                out.append(item)
        return out

    def detail(self, item):
        r = self.fetcher.get(item["url"])
        if r.status_code >= 400:
            return {"active": False}
        s = BeautifulSoup(r.text, "lxml")
        check_blocked(s)
        main = s.select_one("#main_column") or s.body
        for junk in main.select("script, style, svg, form, .panel-body-sponsor, [class*='sponsor'], [id*='ad_']"):
            junk.decompose()
        text = clean(main.get_text("\n"))
        text = re.sub(r"Die passende Absicherung.*?(?=\nKosten)", "", text, flags=re.S)
        title = node_text(s.select_one("h1#sliderTopTitle, h1.headline, h1"))
        imgs = []
        for u in re.findall(r'https://img\.wg-gesucht\.de/media/up/[^"\'\s]+\.large\.(?:jpe?g|png|webp)', r.text, re.I):
            if u not in imgs:
                imgs.append(u)
        out = {"title": title or None, "raw_text": text[:15000], "images": imgs or None, "provider": "Privat (WG-Gesucht)"}
        t = re.sub(r"\s+", " ", text)
        out["price_cold"] = num(m.group(1)) if (m := re.search(r"Miete\s*:\s*(\d[\d.]*)\s*€", t)) else None
        out["costs_extra"] = num(m.group(1)) if (m := re.search(r"Nebenkosten\s*:.*?(\d[\d.]*)\s*€", t[:3000])) else None
        out["price_warm"] = num(m.group(1)) if (m := re.search(r"Gesamtmiete\s*:\s*(\d[\d.]*)\s*€", t)) else None
        out["deposit"] = num(m.group(1)) if (m := re.search(r"Kaution\s*:\s*(\d[\d.]*)\s*€", t)) else None
        out["area"] = num(m.group(1)) if (m := re.search(r"Größe\s*:\s*(\d[\d,.]*)\s*m²", t)) else None
        out["rooms"] = num(m.group(1)) if (m := re.search(r"Zimmer\s*:\s*(\d[\d,.]*)", t)) else None
        if m := re.search(r"frei ab\s*:?\s*(\d{2}\.\d{2}\.\d{4})", t, re.I):
            out["available_from"] = m.group(1)
        if re.search(r"frei bis\s*:?\s*\d{2}\.\d{2}\.\d{4}", t, re.I):
            out["temporary"] = True
        if m := re.search(r"\b(01[0-4]\d\d)\s+Dresden\s*([A-ZÄÖÜ][\wäöüß\-]+)?", t):
            out["zip"] = m.group(1)
            out["district"] = item.get("district") or m.group(2)
            out["address"] = clean(t[max(0, m.start() - 60):m.end()]).split("Adresse")[-1].strip(" :")
        return out
