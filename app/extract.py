"""Regelbasierte Extraktion aus Freitext + Plattenbau-Bewertung."""
import re

NUM = r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[,.]\d+)?)"


def num(s) -> float | None:
    """Deutsche Zahl ('1.234,56', '70,1', '850') -> float."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace("\xa0", "").replace(" ", "")
    m = re.search(NUM, s)
    if not m:
        return None
    t = m.group(1)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", t):
        t = t.replace(".", "")
    return float(t.replace(",", "."))


# Zeilenumbrüche werden zu " | " – Portale schreiben "Kaltmiete | 600 €" oder "600 € | Kaltmiete"
SEP = r"\s*(?:[:|]\s*)*"
EURO = r"\s*(?:€|EUR\b|Euro\b|,-)(?!\s*/\s*(?:m|qm))"


def _money(text, labels, lo=20, hi=10000):
    for label in labels:
        pats = [label + r"(?:[^0-9€|]{0,40}?)" + SEP + NUM + EURO,  # Bezeichnung -> Wert
                NUM + EURO + SEP + r"(?:\(?[^|0-9]{0,15}\)?" + SEP + r")?" + label]  # Wert -> Bezeichnung
        for pat in pats:
            for m in re.finditer(pat, text, re.I):
                v = num(m.group(1))
                if v is not None and lo <= v <= hi:
                    return v
    return None


def _first(pattern, text, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None


NEGATION = r"(?:kein(?:e|en)?|ohne|nicht vorhanden)"
# Wohnungstausch: nur gegen eine andere Wohnung zu haben
SWAP = re.compile(r"wohnungstausch|tauschwohnung|tauschangebot|tauschpartner|\btausch(?:e|en)?\b(?!\s*(?:der|des|von)\b)"
                  r"|zum tausch|im tausch gegen|nur (?:gegen|im) tausch", re.I)


def _has(word_re, text):
    """True wenn vorhanden, False wenn explizit verneint, None wenn nicht erwähnt."""
    if re.search(rf"{NEGATION}\s+(?:\w+\s+)?{word_re}|{word_re}{SEP}(?:nein|nicht vorhanden|keine?r?)\b", text, re.I):
        return False
    if re.search(rf"\b{word_re}", text, re.I):
        return True
    return None


def floor_from(s: str | None) -> int | None:
    if not s:
        return None
    s = s.strip()
    if re.match(r"(EG|Erdgeschoss|Hochparterre|Parterre)", s, re.I):
        return 0
    if re.match(r"(UG|Souterrain|Keller)", s, re.I):
        return -1
    m = re.search(r"-?\d+", s)
    return int(m.group()) if m else None


def extract_text(text: str) -> dict:
    """Holt die üblichen Felder per Regex aus einem Expose-Text."""
    t = re.sub(r"[ \t\xa0]+", " ", text or "")
    t = re.sub(r"(\d)\s*m\s*\n?\s*2\b", r"\1 m²", t)  # hochgestellte 2 (m<sup>2</sup>) wird beim Auslesen zu "m 2"
    t = re.sub(r"\s*\n\s*", " | ", t)
    out = {
        "price_cold": _money(t, ["Kaltmiete", "Nettokaltmiete", r"Miete \(kalt\)", r"Miete:"]),
        "price_warm": _money(t, ["Warmmiete", "Gesamtmiete", "Bruttowarmmiete", r"Miete \(warm\)"]),
        "costs_extra": _money(t, ["Nebenkosten", "Betriebskosten"], lo=10, hi=2000),
        "costs_heat": _money(t, ["Heizkosten"], lo=5, hi=1000),
        "deposit": _money(t, ["Kaution", "Mietsicherheit"], lo=50, hi=20000),
    }
    # Fließtext: "Miete von 351,83 € kalt" / "850 € warm"
    for key, word in (("price_cold", "kalt"), ("price_warm", "warm")):
        if out[key] is None and (m := re.search(NUM + r"\s*(?:€|Euro)\s*" + word + r"\b", t, re.I)):
            out[key] = num(m.group(1))
    a = _first(r"(?:Wohnfläche|Größe|Fläche)(?:\s*\(?ca\.?\)?)?" + SEP + r"(?:ca\.?\s*)?" + NUM + r"\s*(?:m²|m2|qm)", t)
    a = a or _first(NUM + r"\s*(?:m²|m2|qm)" + SEP + r"Wohnfläche", t) or _first(NUM + r"\s*(?:m²|m2|qm)\b", t)
    out["area"] = num(a) if a and 8 <= (num(a) or 0) <= 400 else None
    r = _first(r"\bZimmer(?:anzahl)?" + SEP + r"(\d{1,2}(?:[,.]5)?)(?![\d,.]*\s*(?:m²|€|qm))\b", t) or _first(
        r"\b(\d{1,2}(?:[,.]5)?)" + SEP + r"(?:-\s*)?(?:Zimmer|Zi\.|Raum|R-|RW\b)", t)
    out["rooms"] = num(r) if r and 0 < num(r) <= 12 else None

    fl = _first(r"\b(?:Etage|Geschoss|Stockwerk)" + SEP + r"(EG|Erdgeschoss|Hochparterre|UG|-?\d{1,2})\b(?!\s*m²)", t) or _first(
        r"\b(\d{1,2})\.\s*(?:OG|Obergeschoss|Etage|Geschoss|Stock)\b", t) or _first(r"\b(Erdgeschoss|Hochparterre)\b", t)
    out["floor"] = floor_from(fl)
    ft = _first(r"(?:Etage|Geschoss)\s*:?\s*(?:\d{1,2}|EG)\s*(?:von|/)\s*(\d{1,2})\b", t) or _first(
        r"(?<!Anzahl )(?:Etagen(?:anzahl)?(?:\s*\(gesamt\))?|Geschosse|Stockwerke)" + SEP + r"(\d{1,2})\b", t) or _first(
        r"\b(\d{1,2})" + SEP + r"(?:Stockwerke|Etagen)\b", t) or _first(
        r"\b(\d{1,2})\s*-?\s*(?:geschossig|stöckig|Geschosser|geschossigen|stöckigen)", t)
    out["floors_total"] = int(ft) if ft and 1 <= int(ft) <= 40 else None
    if _first(r"\b(Dachgeschoss)\b", t) and out["floor"] is None and out["floors_total"]:
        out["floor"] = out["floors_total"] - 1
    y = _first(r"Baujahr[^0-9]{0,40}?(1[789]\d\d|20[0-3]\d)", t) or _first(r"(?:erbaut|errichtet|Baujahr)\s*(?:im Jahr|ca\.)?\s*(1[89]\d\d|20[0-3]\d)", t)
    out["year_built"] = int(y) if y else None

    out["balcony"] = _has(r"(?:Balkon|Loggia)", t)
    out["terrace"] = _has(r"(?:Terrasse|Dachterrasse)", t)
    out["garden"] = _has(r"(?:Garten|Gartennutzung|Gartenanteil)", t)
    out["kitchen"] = _has(r"(?:Einbauküche|EBK)", t)
    out["elevator"] = _has(r"(?:Aufzug|Fahrstuhl|Personenaufzug|Lift)\b", t)
    first_line = (text or "").strip().split("\n", 1)[0]
    if re.search(r"möbliert|furnished", first_line, re.I) and not re.search(r"(?:un|teil)möbliert", first_line, re.I):
        out["furnished"] = True  # steht schon im Titel
    elif re.search(r"\b(?:un|teil)möbliert|unmöbiliert", t, re.I):
        out["furnished"] = False
    elif re.search(r"\b(?:voll)?möbliert|\bfurnished|Vollmöbliert", t, re.I):
        out["furnished"] = True
    else:
        out["furnished"] = None
    if re.search(r"(?:WBS|Wohnberechtigungsschein)(?!\s*-?\s*70)(?:\s+\w+){0,3}\s+(?:erforderlich|notwendig|nötig|benötigt|vorausgesetzt)|nur mit (?:WBS|Wohnberechtigungsschein)", t, re.I):
        out["wbs_required"] = True
    if re.search(r"\b(?:befristet|Zwischenmiete|zur Untermiete|frei bis|Mietdauer bis|auf Zeit|Zeitwohnen|Monteur|Zeitmietvertrag)", t, re.I) and not re.search(r"unbefristet", t, re.I):
        out["temporary"] = True
    # Kleinanzeigen-Feld "Tauschangebot: Kein Tausch" und andere Verneinungen nicht als Tausch werten
    if SWAP.search(re.sub(r"Tauschangebot\W*(?:Kein Tausch|nein)\b|\bkein(?:e|en)?\s+(?:Wohnungs)?tausch\w*", "", t, flags=re.I)):
        out["swap"] = True
    av = _first(r"(?:verfügbar ab|frei ab|bezugsfrei ab|Bezug ab|Einzug ab)\s*:?\s*([0-9]{1,2}\.[0-9]{1,2}\.(?:[0-9]{2,4})?|sofort|nach Vereinbarung|\w+ 20\d\d)", t)
    out["available_from"] = av
    out["zip"] = _first(r"\b(01[0-4]\d\d)\b", t)
    return out


def merge(base: dict, *extra: dict) -> dict:
    """Füllt leere Felder in base mit Werten aus extra (erstes gewinnt)."""
    out = dict(base)
    for e in extra:
        for k, v in (e or {}).items():
            if out.get(k) in (None, "", []) and v not in (None, "", []):
                out[k] = v
    return out


# --- Plattenbau-Erkennung -------------------------------------------------------------

# Dresdner Stadtteile mit hohem Anteil an DDR-Großwohnsiedlungen (Gewicht 0-40)
PLATTE_DISTRICTS = {
    "gorbitz": 40, "prohlis": 40, "reick": 35, "zschertnitz": 30, "leubnitz-neuostra": 30, "neuostra": 30,
    "johannstadt": 18, "seidnitz": 15, "strehlen": 10, "leuben": 10, "trachau": 5, "klotzsche": 5,
    "nickern": 25, "kleinzschachwitz": 5, "wilsdruffer vorstadt": 8, "seevorstadt": 8, "pirnaische vorstadt": 15,
    "großzschachwitz": 10, "dobritz": 10, "omsewitz": 30, "wölfnitz": 15, "südvorstadt-ost": 12,
}
# Grobe Zuordnung PLZ -> Stadtteile (Fallback, wenn das Inserat keinen Stadtteil nennt)
DRESDEN_ZIP = {
    "01067": "Altstadt/Friedrichstadt", "01069": "Südvorstadt/Seevorstadt", "01097": "Innere Neustadt/Leipziger Vorstadt",
    "01099": "Äußere Neustadt/Albertstadt", "01108": "Weixdorf", "01109": "Klotzsche/Hellerau", "01127": "Pieschen",
    "01129": "Trachau/Mickten", "01139": "Kaditz/Übigau", "01156": "Cossebaude/Gompitz", "01157": "Cotta/Briesnitz",
    "01159": "Löbtau", "01169": "Gorbitz", "01187": "Plauen", "01189": "Coschütz/Gittersee", "01217": "Räcknitz/Zschertnitz",
    "01219": "Strehlen/Reick", "01237": "Prohlis/Reick", "01239": "Prohlis-Süd/Nickern", "01257": "Leuben/Niedersedlitz",
    "01259": "Großzschachwitz/Kleinzschachwitz", "01277": "Striesen/Gruna/Seidnitz", "01279": "Laubegast/Tolkewitz",
    "01307": "Johannstadt", "01309": "Striesen/Blasewitz", "01324": "Loschwitz/Weißer Hirsch", "01326": "Pillnitz/Wachwitz",
    "01328": "Bühlau/Weißig",
}
PLATTE_ZIPS = {"01169": 30, "01237": 30, "01239": 25, "01219": 10, "01307": 10, "01217": 10}

PLATTE_POS = [
    (r"plattenbau", 55, "Text erwähnt 'Plattenbau'"),
    (r"\bWBS\s*-?\s*70\b", 55, "Bautyp WBS 70"),
    (r"\b(?:Typ|Bautyp)\s*P\s*-?\s*2\b|\bP2-?Bau", 50, "Bautyp P2"),
    (r"\bIW\s*-?\s*\d{2}\b|\bQ\s*-?\s*6\b", 30, "DDR-Typenbau"),
    (r"Typenbau|Großwohnsiedlung|Neubaugebiet der (?:70|80)er|DDR-(?:Neubau|Bau)", 35, "DDR-Typenbau/Großsiedlung"),
    (r"(?:1[01]|[5-9]|1[2-9])\s*-?\s*(?:geschossig|Geschosser|stöckig)", 15, "viele Geschosse"),
    (r"Hochhaus|Punkthochhaus|Wohnhochhaus", 25, "Hochhaus"),
    (r"(?:70er|80er|siebziger|achtziger)\s*Jahre", 20, "Baujahr 70er/80er erwähnt"),
    (r"\bVonovia\b|Südost Woba|WOBA", 10, "typischer Plattenbau-Bestandshalter"),
]
PLATTE_NEG = [
    (r"Altbau|Gründerzeit|Jugendstil|Stuck|Dielen|Stadtvilla|Villa|Denkmal|Fachwerk", -35, "Altbau-Merkmale"),
    (r"Neubau(?!gebiet|block|viertel der)|Erstbezug nach Neubau|Neubauprojekt|KfW\s*\d+|Niedrigenergie|Passivhaus", -25, "Neubau-Merkmale"),
    (r"Einfamilienhaus|Doppelhaushälfte|Reihenhaus|Zweifamilienhaus|Mehrfamilienhaus mit (?:[2-6]|zwei|drei|vier|fünf|sechs) (?:Wohn)?(?:einheiten|Parteien)", -25, "kleines Haus"),
]


def plattenbau(item: dict) -> tuple[int, list[str]]:
    """Gibt Score 0-100 (Wahrscheinlichkeit Plattenbau) und Gründe zurück."""
    text = " ".join(str(item.get(k) or "") for k in ("title", "description", "raw_text", "provider"))
    score, reasons = 10, []
    for pat, w, why in PLATTE_POS + PLATTE_NEG:
        if re.search(pat, text, re.I):
            score += w
            reasons.append(f"{'+' if w > 0 else ''}{w} {why}")
    y = item.get("year_built")
    if y:
        if 1960 <= y <= 1991:
            score += 35
            reasons.append(f"+35 Baujahr {y}")
        elif y < 1950 or y >= 1995:
            score -= 40
            reasons.append(f"-40 Baujahr {y}")
    ft = item.get("floors_total") or 0
    fl = item.get("floor") or 0
    if ft >= 9 or fl >= 8:
        score += 25
        reasons.append(f"+25 Hochhaus ({max(ft, fl + 1)} Etagen)")
    elif ft >= 5 or fl >= 5:
        if y is None or 1960 <= y <= 1991:
            score += 10
            reasons.append("+10 5-8 Geschosse")
    dist = (item.get("district") or "").lower() + " " + (item.get("address") or "").lower()
    best = max(((w, d) for d, w in PLATTE_DISTRICTS.items() if d in dist), default=None)
    if best:
        score += best[0]
        reasons.append(f"+{best[0]} Stadtteil {best[1].title()}")
    elif (w := PLATTE_ZIPS.get(item.get("zip") or "")):
        score += w
        reasons.append(f"+{w} PLZ {item['zip']} ({DRESDEN_ZIP[item['zip']]})")
    bt = (item.get("building_type") or "").lower()
    if bt == "plattenbau":
        score += 40
        reasons.append("+40 KI: Plattenbau")
    elif bt in ("altbau", "neubau", "haus"):
        score -= 25
        reasons.append(f"-25 KI: {bt}")
    return max(0, min(100, score)), reasons
