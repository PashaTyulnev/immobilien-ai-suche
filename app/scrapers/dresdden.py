"""dresdden.de – Johannes & Partner Immobilien (Dresdner Makler mit eigenen Mietangeboten)."""
import re

from ..extract import floor_from, num
from .base import Scraper, is_dresden, node_text

BASE = "https://www.dresdden.de/"
SEARCH = BASE + "object_list.php?search_type=Alle_Miete&search_rooms=0&offset={offset}"
PROVIDER = "Johannes & Partner Immobilien"
PHONE = "0351 3144170"

# "Label: Wert" in der Objektbeschreibung -> Feld
LABELS = {
    "Wohnfläche": "area", "Kaltmiete": "price_cold", "Warmmiete": "price_warm", "NK-vorausz.": "costs_extra",
    "Heizkosten": "costs_heat", "Etage": "floor", "Frei ab": "available_from", "Baujahr": "year_built",
}
KEYS = ["Adresse", "ID", "Wohnfläche", "Zimmer", "Etage", "Balkon", "Objektart", "Einbauküche", "Frei ab", "Aufzug/Lift",
        "PKW-Stellplatz", "Kaltmiete", "Kaution", "NK-vorausz.", "Heizkosten", "Warmmiete", "Beschreibung", "Baujahr",
        "Objektzustand", "Heizungsart", "Befeuerungsart", "Energiebedarf", "Typ", "Energieeffizienzklasse"]


def facts(text: str) -> dict:
    """Zerlegt 'Wohnfläche: 43,60 m² Zimmer: 2-Zimmer-Wohnung …' anhand der bekannten Bezeichnungen."""
    pat = re.compile(r"(?<!\w)(" + "|".join(re.escape(k) for k in KEYS) + r")\s*:\s*")
    parts = pat.split(text)
    return {parts[i]: parts[i + 1].strip(" ,") for i in range(1, len(parts) - 1, 2)}


class Dresdden(Scraper):
    name = "dresdden"
    label = "dresdden.de (Johannes & Partner)"

    def search(self, page):
        r = self.fetcher.get(SEARCH.format(offset=page - 1))
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        ids = list(dict.fromkeys(re.findall(r"show_object=(\d+)", r.text)))
        return [{"ext_id": i, "url": f"{BASE}details.php?show_object={i}"} for i in ids]

    def detail(self, item):
        s = self.soup(item["url"])
        text = " ".join(s.get_text(" ").split())
        start = text.find("Adresse:")
        if start < 0:
            return {"active": False}
        body = text[start:].split("Exposé ist nur zur Information")[0]
        f = facts(body)
        out = {"raw_text": body, "provider": PROVIDER, "phones_found": [PHONE]}
        for label, field in LABELS.items():
            v = f.get(label)
            if not v or v.strip() == "-":
                continue
            if field == "floor":
                out[field] = floor_from(v)
            elif field == "available_from":
                out[field] = v
            elif field == "year_built":
                out[field] = int(num(v)) if num(v) else None
            else:
                out[field] = num(v)
        if not (out.get("price_cold") or out.get("price_warm")):
            return {"active": False}  # Kaufobjekt aus dem Tipp-Kasten
        if rooms := re.search(r"(\d+(?:[,.]5)?)", f.get("Zimmer", "")):
            out["rooms"] = num(rooms.group(1))
        out["balcony"] = True if re.search(r"Balkon|Loggia", f.get("Balkon", ""), re.I) else None
        out["kitchen"] = None if f.get("Einbauküche", "-").strip() == "-" else True
        out["elevator"] = None if f.get("Aufzug/Lift", "-").strip() == "-" else True
        out["description"] = f.get("Beschreibung")
        # "01257 Dresden - Leuben , Dieselstraße 4 WE 17"
        if m := re.match(r"(\d{5})\s+([^-,]+?)\s*-\s*([^,]+?)\s*,\s*(.+)", f.get("Adresse", "")):
            out["zip"], city, out["district"], street = m.group(1), m.group(2), m.group(3), m.group(4)
            out["address"] = f"{street}, {out['zip']} {city}"
            if not is_dresden(out["zip"]):
                return {"active": False}
        title = node_text(s.select_one("h1, h2"))
        out["title"] = title if title and "Details zu Objekt" not in title else \
            f"{f.get('Zimmer', 'Wohnung')} in {out.get('district') or 'Dresden'}"
        imgs = []
        for img in s.select('img[src*="/pics/"]'):
            u = re.sub(r"\?.*$", "", img["src"])
            if u not in imgs:
                imgs.append(u)
        out["images"] = sorted(imgs, key=lambda u: int(re.search(r"_(\d+)\.", u).group(1)) if re.search(r"_(\d+)\.", u) else 99) or None
        return out
