"""Wohnungsgenossenschaften und WiD: eigene Webseiten mit Listenseite + Detailseiten.

Ein gemeinsamer Scraper, je Vermieter nur konfiguriert. Die Detailseiten liest die normale
Text-Extraktion (Kaltmiete, Gesamtmiete, Fläche, Zimmer, Etage …). Eine Seite zählt nur als
Wohnung, wenn Preis UND Fläche erkennbar sind – so landen keine Navigations-/Infoseiten als Inserat.
"""
import re
from urllib.parse import urljoin

from ..extract import extract_text
from .base import Scraper, check_blocked, clean, is_dresden, node_text

SIMILAR = re.compile(r"\n(?:Ähnliche (?:Immobilien|Objekte|Angebote)|Diese Immobilien könnten Sie auch interessieren|"
                     r"Weitere (?:Angebote|Objekte|Immobilien)|Das könnte Sie auch interessieren)", re.I)
SIMILAR_HTML = re.compile(r"Ähnliche (?:Immobilien|Objekte|Angebote)|Diese Immobilien könnten Sie auch interessieren", re.I)
NOT_FLAT = re.compile(r"gewerbe|laden(?:fläche|lokal)?\b|büro|praxis|\bEKZ\b|laufkundschaft|stellplatz|garage|lager(?:raum|halle)", re.I)
IMG_SKIP = re.compile(r"logo|icon|sprite|avatar|favicon|placeholder|\.svg|google|maps|-\d{2,3}x\d{2,3}\.", re.I)


class SiteScraper(Scraper):
    list_urls: list[str] = []
    detail_re = r""  # Muster der Detail-Links (auf absolute URLs angewandt)
    image_re = r""  # optional: Muster der Wohnungsfotos
    provider = ""
    note = ""  # Hinweis, der an die Beschreibung angehängt wird (z.B. Genossenschaftsanteile)
    rent_only = True  # Seiten mit "Kaufpreis" überspringen
    image_needs_id = False  # nur Fotos, deren Adresse die Objektnummer enthält
    html_image_re = r""  # optional: Foto-Adressen direkt aus dem Quelltext

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
        text = SIMILAR.split(text)[0]  # Empfehlungsblock ("Ähnliche Immobilien" …) gehört nicht zum Inserat
        f = extract_text(text)
        if not (f.get("price_cold") or f.get("price_warm")) or not f.get("area"):
            return {"active": False}  # keine Wohnung (Info-/Navigationsseite) oder schon vergeben
        if self.rent_only and re.search(r"Kaufpreis|Kaufangebot|zum Kauf\b", text, re.I):
            return {"active": False}  # Makler mischen Kauf- und Mietobjekte
        zips = re.findall(r"\b(0\d{4})\s+[A-ZÄÖÜ]", text)
        if zips and not any(is_dresden(z) for z in zips):
            return {"active": False}  # außerhalb Dresdens (z.B. Meißen, Freiberg)
        # erste aussagekräftige Überschrift vor dem Empfehlungsblock (WiD: <h1> ist nur "Mietangebot"),
        # sonst der Seitentitel bis zum ersten "|"
        flat = " ".join(text.split())
        heads = [" ".join(node_text(h).split()) for h in main.select("h1, h2, h3")]
        heads = [h for h in heads if h and h in flat]
        page_title = (s.title.get_text(" ", strip=True).split("|")[0].strip() if s.title else "") or item["url"].rstrip("/").split("/")[-1]
        title = next((h for h in heads if len(h) > 15 and not re.fullmatch(r"(Miet)?angebot|Mietobjekt|Exposé", h, re.I)), page_title)
        if NOT_FLAT.search(title) and not re.search(r"wohnung|zimmer|maisonette|apartment|\d\s*-?\s*R(?:W|aum)", title, re.I):
            return {"active": False}  # Gewerbe, Laden, Stellplatz … (nicht: "Wohnung mit Tiefgarage")
        imgs = []
        for img in main.select("img"):
            src = img.get("data-src") or img.get("src") or ""
            u = urljoin(item["url"], src)
            if src and not IMG_SKIP.search(u) and (re.search(self.image_re, u) if self.image_re else re.search(r"\.(jpe?g|png|webp)", u, re.I)):
                if self.image_needs_id and re.sub(r"\D", "", item["ext_id"]) not in u:
                    continue  # Foto eines anderen (empfohlenen) Objekts
                if u not in imgs:
                    imgs.append(u)
        if self.html_image_re:  # Galerie nur als Adressen im Quelltext (z.B. Bilddienst ohne Dateiendung)
            head = SIMILAR_HTML.split(r.text)[0]
            imgs += [u for u in dict.fromkeys(re.findall(self.html_image_re, head)) if u not in imgs]
        # gleiche Fotos in mehreren Zuschnitten (TYPO3: "csm_name_<hash>.jpg") nur einmal
        seen, uniq = set(), []
        for u in imgs:
            key = re.sub(r"_[0-9a-f]{10}(\.\w+)$", r"\1", u.split("/")[-1])
            if key not in seen:
                seen.add(key)
                uniq.append(u)
        imgs = uniq
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


# ---------- Makler mit eigenen Angeboten (aus dem Makler-Crawler) ----------

class EdelWert(SiteScraper):  # Propstack
    name, label = "edelwert", "EdelWert Immobilien"
    provider = "EdelWert Immobilien GmbH"
    list_urls = ["https://edelwert-immobilien.de/immobilien"]
    detail_re = r"edelwert-immobilien\.de/immobilie/ps-\d+$"


class ExitImmo(SiteScraper):
    name, label = "exit", "EXIT Immobilien"
    provider = "EXIT Immobilien"
    list_urls = ["https://www.exit-immobilien.de/immobilienangebote/"]
    detail_re = r"exit-immobilien\.de/immobilie/[^/]+/$"
    html_image_re = r"https://immo\.screenwork\.de/images/\d+/\d+/cache/[\d/]+/800-600[\w-]*"


class Viarealis(SiteScraper):
    name, label = "viarealis", "VIAREALIS"
    provider = "VIAREALIS GmbH"
    list_urls = ["https://viarealis.de/"]
    detail_re = r"viarealis\.de/objekte/[^/]+/$"


class BeateProtze(SiteScraper):
    name, label = "protze", "Beate Protze Immobilien"
    provider = "Beate Protze Immobilien GmbH"
    list_urls = ["https://www.beate-protze-immobilien.de/wa"]
    detail_re = r"beate-protze-immobilien\.de/expose/BPI_\d+$"
    image_needs_id = True  # Fotos heißen z.B. "…-bpi10517-…"; auf der Seite stehen auch Fotos ähnlicher Objekte


class UniplanTerraCotta(Scraper):
    """uniplan: Neubauprojekt TerraCotta in Dresden – Wohnungen stehen als Tabelle (Exposés nur als PDF)."""
    name, label = "uniplan", "uniplan TerraCotta (Neubau)"
    url = "https://www.uniplan-gruppe.com/de/immobilien/dresden-terracotta"
    fetch_details = False  # alles steht in der Tabelle

    def search(self, page):
        if page > 1:
            return []
        from ..extract import floor_from, num
        s = self.soup(self.url)
        text = " ".join(s.get_text(" ").split())
        zips = re.findall(r"\b(01[0-4]\d\d)\s+Dresden", text)
        street = re.search(r"([A-ZÄÖÜ][\wäöüß.-]+(?:straße|str\.|weg|platz|allee|ring)\s*\d*[a-z]?)\s*,?\s*01[0-4]\d\d\s+Dresden", text)
        imgs = [urljoin(self.url, i.get("data-src") or i["src"]) for i in s.select("img[src], img[data-src]")
                if re.search(r"\.(jpe?g|webp)", i.get("data-src") or i.get("src") or "", re.I) and not IMG_SKIP.search(i.get("src") or "")]
        out = []
        for tr in s.select("table tr"):
            c = [" ".join(td.get_text(" ").split()) for td in tr.select("td")]
            if len(c) < 8 or c[7].lower() != "frei":
                continue  # Kopfzeile oder schon vermietet
            unit, move_in, rooms, area, floor, cold, warm = c[:7]
            pdf = tr.select_one('a[href*=".pdf"]')
            out.append({
                "ext_id": unit, "url": urljoin(self.url, pdf["href"]) if pdf else self.url,
                "title": f"TerraCotta Neubau – {rooms}-Zimmer-Wohnung {unit}",
                "rooms": num(rooms), "area": num(area), "floor": floor_from(floor),
                "price_cold": num(cold), "price_warm": num(warm), "available_from": move_in,
                "zip": zips[0] if zips else None, "address": (street.group(1) + ", " + zips[0] + " Dresden") if street and zips else None,
                "provider": "uniplan Management GmbH", "images": imgs[:8] or None,
                "description": f"Neubauprojekt TerraCotta (uniplan) – Wohnung {unit}, {rooms} Räume, {area}, Etage {floor}, "
                               f"Kaltmiete {cold}, Warmmiete {warm}, Bezug {move_in}. Exposé als PDF.",
                "year_built": 2026,
            })
        return out

    def detail(self, item):
        return {}


MAKLER = (EdelWert, ExitImmo, Viarealis, BeateProtze, UniplanTerraCotta)
