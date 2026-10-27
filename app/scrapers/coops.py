"""Wohnungsgenossenschaften und WiD: eigene Webseiten mit Listenseite + Detailseiten.

Ein gemeinsamer Scraper, je Vermieter nur konfiguriert. Die Detailseiten liest die normale
Text-Extraktion (Kaltmiete, Gesamtmiete, Fläche, Zimmer, Etage …). Eine Seite zählt nur als
Wohnung, wenn Preis UND Fläche erkennbar sind – so landen keine Navigations-/Infoseiten als Inserat.
"""
import re
from urllib.parse import urljoin

from ..extract import extract_text
from .base import Scraper, check_blocked, clean, node_text

IMG_SKIP = re.compile(r"logo|icon|sprite|avatar|favicon|placeholder|\.svg|google|maps|-\d{2,3}x\d{2,3}\.", re.I)


class SiteScraper(Scraper):
    list_urls: list[str] = []
    detail_re = r""  # Muster der Detail-Links (auf absolute URLs angewandt)
    image_re = r""  # optional: Muster der Wohnungsfotos
    provider = ""
    note = ""  # Hinweis, der an die Beschreibung angehängt wird (z.B. Genossenschaftsanteile)

    def search(self, page):
        if page > 1:
            return []
        out = {}
        for lu in self.list_urls:
            s = self.soup(lu)
            for a in s.select("a[href]"):
                u = urljoin(lu, a["href"]).split("#")[0]
                if re.search(self.detail_re, u) and u not in out:
                    slug = re.sub(r"\W+", "-", u.rstrip("/").split("/")[-1].replace(".html", ""))[:80]
                    out[u] = {"ext_id": slug, "url": u}
        return list(out.values())

    def detail(self, item):
        r = self.fetcher.get(item["url"])
        if r.status_code == 404:
            return {"active": False}
        from bs4 import BeautifulSoup
        s = BeautifulSoup(r.text, "lxml")
        check_blocked(s)
        main = s.select_one("main, article, #content, .content") or s.body
        for junk in main.select("script, style, nav, header, footer, form, noscript, iframe"):
            junk.decompose()
        text = clean(main.get_text("\n"))
        f = extract_text(text)
        if not (f.get("price_cold") or f.get("price_warm")) or not f.get("area"):
            return {"active": False}  # keine Wohnung (Info-/Navigationsseite) oder schon vergeben
        # erste aussagekräftige Überschrift (WiD: <h1> ist nur "Mietangebot")
        heads = [" ".join(node_text(h).split()) for h in main.select("h1, h2, h3")]
        title = next((h for h in heads if len(h) > 15 and not re.fullmatch(r"(Miet)?angebot|Mietobjekt|Exposé", h, re.I)),
                     heads[0] if heads else item["url"].rstrip("/").split("/")[-1])
        imgs = []
        for img in main.select("img"):
            src = img.get("data-src") or img.get("src") or ""
            u = urljoin(item["url"], src)
            if src and not IMG_SKIP.search(u) and (re.search(self.image_re, u) if self.image_re else re.search(r"\.(jpe?g|png|webp)", u, re.I)):
                if u not in imgs:
                    imgs.append(u)
        return {
            "title": title[:200], "raw_text": text[:15000], "provider": self.provider,
            "description": (clean(text)[:3000] + (f"\n\n{self.note}" if self.note else "")),
            "images": imgs or None,
        }


class WiD(SiteScraper):
    name, label = "wid", "WiD Wohnen in Dresden"
    provider = "WiD Wohnen in Dresden"
    list_urls = ["https://www.wid-dresden.de/vermietung-mietangebote.html"]
    detail_re = r"wid-dresden\.de/mietobjekt/\d+\.html$"
    image_re = r"files/api/mietobjekte/"


class SWG(SiteScraper):
    name, label = "swg", "SWG (Sächs. Wohnungsgenossenschaft)"
    provider = "Sächsische Wohnungsgenossenschaft Dresden eG"
    list_urls = ["https://swg-dresden.de/wohnungsangebote/"]
    detail_re = r"swg-dresden\.de/apartment/[^/]+/$"
    image_re = r"/wp-content/apartment/"
    note = "Genossenschaft: Mitgliedschaft mit Genossenschaftsanteilen nötig."


class WGAufbau(SiteScraper):
    name, label = "wgaufbau", "WG Aufbau Dresden"
    provider = "WG Aufbau Dresden eG"
    list_urls = ["https://www.wgaufbau-dresden.de/objektsuche/wohnung/"]
    detail_re = r"wgaufbau-dresden\.de/objektsuche/wohnung/\d{6,}-[^/]+/$"
    note = "Genossenschaft: Mitgliedschaft mit Genossenschaftsanteilen nötig."


class EWG(SiteScraper):
    name, label = "ewg", "EWG Dresden (Eisenbahner-WBG)"
    provider = "Eisenbahner-Wohnungsbaugenossenschaft Dresden eG"
    list_urls = ["https://www.ewg-dresden.de/wohnungen/"]
    detail_re = r"ewg-dresden\.de/immobilie/[^/]+/$"
    note = "Genossenschaft: Mitgliedschaft mit Genossenschaftsanteilen nötig."


class WGS(SiteScraper):
    name, label = "wgs", "WG Glückauf Süd Dresden"
    provider = "Wohnungsgenossenschaft Glückauf Süd Dresden eG"
    list_urls = ["https://www.wgs-dresden.de/objektsuche"]
    detail_re = r"wgs-dresden\.de/objektsuche/details/[\w-]+$"
    note = "Genossenschaft: Mitgliedschaft mit Genossenschaftsanteilen nötig."


class WGJ(SiteScraper):
    # Zurzeit keine Angebote online ("Keine Ergebnisse gefunden"); Muster bewusst weit,
    # die Preis+Fläche-Prüfung im Exposé filtert Nicht-Wohnungen heraus.
    name, label = "wgj", "WG Johannstadt"
    provider = "Wohnungsgenossenschaft Johannstadt eG"
    list_urls = ["https://www.wgj.de/wohnungen.html"]
    detail_re = r"wgj\.de/(?:wohnungen|wohnung|objekt|mietangebot|expose)[\w-]*/[\w.-]+$|wgj\.de/wohnungen/[\w.-]+\.html$"
    note = "Genossenschaft: Mitgliedschaft mit Genossenschaftsanteilen nötig."


COOPS = (WiD, SWG, WGAufbau, EWG, WGS, WGJ)
