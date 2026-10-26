"""Konfiguration: Defaults + optionale Überschreibung durch config.json im Projektordner."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "wohnungen.db"
CONFIG_PATH = ROOT / "config.json"

DEFAULTS = {
    # Welche Quellen aktiv sind, wie viele Ergebnisseiten maximal geladen werden, optional eigene Pause (delay)
    "sources": {
        "immowelt": {"enabled": True, "max_pages": 40, "details": False},  # Exposés -> HTTP 403 nach ~25 Abrufen
        "kleinanzeigen": {"enabled": True, "max_pages": 40, "delay": 2.5},
        "ohnemakler": {"enabled": True, "max_pages": 10},
        "wggesucht": {"enabled": True, "max_pages": 6, "delay": 6},  # zeigt schnell Captchas
        "immobiliende": {"enabled": True, "max_pages": 15},
        "dresdden": {"enabled": True, "max_pages": 5},  # Johannes & Partner Immobilien
        # ImmoScout24 über Suchauftrag-Mails per IMAP; App-Passwort in config.json eintragen, dann enabled: true
        "immoscout": {"enabled": False, "details": False, "user": "", "app_password": "",
                      "imap_host": "imap.gmail.com", "days": 14},  # ältere Mail-Inserate gelten als vergeben
    },
    # Sekunden zwischen zwei Anfragen an dieselbe Seite (höflich bleiben, sonst Sperre)
    "request_delay": 1.5,
    # Automatisch alle X Stunden scrapen, solange der Server läuft (0 = aus)
    "auto_scrape_hours": 4,
    # Lokales LLM über Ollama zum Nachextrahieren fehlender Felder + Gebäudetyp
    "llm": {
        "enabled": True,
        "url": "http://localhost:11434",
        "model": "qwen2.5:14b",
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return _merge(DEFAULTS, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return DEFAULTS
