"""Strukturierte Extraktion mit einem lokalen Modell über Ollama."""
import json

import httpx

FIELDS = {
    "price_cold": ("number", "Kaltmiete in Euro pro Monat"),
    "price_warm": ("number", "Warmmiete/Gesamtmiete in Euro pro Monat"),
    "costs_extra": ("number", "Nebenkosten (Betriebskosten) in Euro"),
    "costs_heat": ("number", "Heizkosten in Euro, falls separat"),
    "deposit": ("number", "Kaution in Euro"),
    "area": ("number", "Wohnfläche in m²"),
    "rooms": ("number", "Anzahl Zimmer"),
    "floor": ("integer", "Etage der Wohnung, Erdgeschoss = 0"),
    "floors_total": ("integer", "Anzahl Geschosse des Gebäudes"),
    "year_built": ("integer", "Baujahr des Gebäudes"),
    "balcony": ("boolean", "Balkon oder Loggia vorhanden"),
    "terrace": ("boolean", "Terrasse vorhanden"),
    "garden": ("boolean", "eigener Garten oder Gartennutzung"),
    "kitchen": ("boolean", "Einbauküche vorhanden"),
    "elevator": ("boolean", "Aufzug vorhanden"),
    "furnished": ("boolean", "Wohnung wird voll möbliert vermietet (teilmöbliert/nur Küche = false)"),
    "wbs_required": ("boolean", "Wohnberechtigungsschein (WBS) erforderlich"),
    "temporary": ("boolean", "nur befristet / Zwischenmiete / Wohnen auf Zeit"),
    "swap": ("boolean", "Wohnungstausch: nur im Tausch gegen eine andere Wohnung zu haben"),
    "available_from": ("string", "Bezugstermin, z.B. 'sofort' oder '01.12.2026'"),
    "district": ("string", "Dresdner Stadtteil"),
}
BUILDING_TYPES = ["plattenbau", "altbau", "sanierter_altbau", "neubau", "nachkriegsbau", "haus", "unbekannt"]

SCHEMA = {
    "type": "object",
    "properties": {
        **{k: {"type": [t, "null"]} for k, (t, _) in FIELDS.items()},
        "building_type": {"type": "string", "enum": BUILDING_TYPES},
        "summary": {"type": "string"},
    },
    "required": [*FIELDS, "building_type", "summary"],
}

PROMPT = """Du extrahierst Daten aus einem deutschen Mietwohnungs-Inserat in Dresden.
Antworte NUR mit JSON nach Schema. Unbekannte Werte = null. Nichts erfinden.
Beziehe dich nur auf DIESE Wohnung, ignoriere ähnliche/weitere Angebote im Text.

Felder:
{fields}
- building_type: Gebäudetyp. "plattenbau" = DDR-Typenbau/Großwohnsiedlung (WBS 70, P2, 1960-1990, oft 5-17 Geschosse,
  Gorbitz/Prohlis/Reick/Johannstadt), "altbau" = vor 1949 unsaniert, "sanierter_altbau" = Gründerzeit/Altbau saniert,
  "nachkriegsbau" = 1950er Jahre, "neubau" = ab 1991, "haus" = Ein-/Zweifamilienhaus, sonst "unbekannt".
- summary: 1-2 sachliche Sätze auf Deutsch: was ist das Besondere / was fällt negativ auf.

INSERAT:
{text}"""


def available(cfg: dict) -> bool:
    try:
        r = httpx.get(cfg["url"] + "/api/tags", timeout=3)
        return r.status_code == 200 and any(m["name"] == cfg["model"] for m in r.json().get("models", []))
    except httpx.HTTPError:
        return False


def extract(cfg: dict, item: dict) -> dict:
    text = "\n".join(str(x) for x in (item.get("title"), item.get("address"), item.get("district") and f"Stadtteil: {item['district']}",
                                       item.get("raw_text") or item.get("description")) if x)[:7000]
    fields = "\n".join(f"- {k}: {desc}" for k, (_, desc) in FIELDS.items())
    r = httpx.post(cfg["url"] + "/api/chat", timeout=300, json={
        "model": cfg["model"],
        "messages": [{"role": "user", "content": PROMPT.format(fields=fields, text=text)}],
        "format": SCHEMA,
        "stream": False,
        "keep_alive": "15m",
        "options": {"temperature": 0, "num_ctx": 8192},
    })
    r.raise_for_status()
    data = json.loads(r.json()["message"]["content"])
    return {k: v for k, v in data.items() if v not in (None, "")}
