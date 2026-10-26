import re

from bs4 import BeautifulSoup

from ..http import Fetcher

# Gesuche sind für die Suche nutzlos (Tauschangebote werden markiert und per Filter ausgeblendet)
SKIP_TITLE = re.compile(r"^\W*(suche|gesucht|wir suchen|ich suche)\b", re.I)


class Scraper:
    name = ""
    label = ""
    tracks_removal = True  # fehlt ein Inserat in den Ergebnissen, gilt es als offline

    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher

    def search(self, page: int) -> list[dict]:
        """Ergebnisseite page (1-basiert) -> Liste von Teil-Inseraten mit mindestens ext_id und url."""
        raise NotImplementedError

    def detail(self, item: dict) -> dict:
        """Expose laden -> zusätzliche Felder, 'raw_text' und 'images'."""
        raise NotImplementedError

    # Hilfen
    def soup(self, url: str) -> BeautifulSoup:
        r = self.fetcher.get(url)
        if r.status_code in (401, 403, 429):
            raise Blocked(f"HTTP {r.status_code} – Portal blockt gerade, später erneut versuchen")
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} für {url}")
        s = BeautifulSoup(r.text, "lxml")
        check_blocked(s)
        return s


class Blocked(RuntimeError):
    """Portal zeigt eine Bot-Prüfung/Captcha – Quelle für diesen Lauf abbrechen."""


def check_blocked(s: BeautifulSoup):
    title = (s.title.get_text(strip=True) if s.title else "").lower()
    body = s.body.get_text(" ", strip=True)[:2000] if s.body else ""
    if (title in ("überprüfung", "just a moment...", "access denied") or "captcha" in title
            or "dass Sie ein Mensch sind" in body or "dass du ein Mensch bist" in body):
        raise Blocked(f"Bot-Prüfung/Captcha ('{title}') – später erneut versuchen")


def clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def node_text(node) -> str:
    return clean(node.get_text("\n")) if node else ""


def is_dresden(zip_code: str | None) -> bool:
    from ..extract import DRESDEN_ZIP
    return zip_code in DRESDEN_ZIP or zip_code == "01465"  # 01465 = Langebrück/Schönborn
