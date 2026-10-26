"""Match-Wert aus Likes/Dislikes: kleines Modell über Eckdaten, Stadtteil, Ausstattung und Text."""
import re
from collections import Counter

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from . import db

# Zahlenmerkmale mit Beschriftung für (niedriger Wert, hoher Wert)
NUMERIC = {"warm": ("günstige Miete", "höhere Miete"), "area": ("kompakt", "viel Fläche"),
           "rooms": ("wenige Zimmer", "mehr Zimmer"), "floor": ("weiter unten", "weiter oben"),
           "sqm": ("günstiger €/m²", "höherer €/m²"), "year_built": ("älteres Haus", "neueres Haus"),
           "plattenbau_score": ("kein Plattenbau", "plattenbau-artig")}
BOOLS = {"balcony": "Balkon", "terrace": "Terrasse", "garden": "Garten", "kitchen": "Einbauküche",
         "elevator": "Aufzug", "furnished": "möbliert", "temporary": "befristet"}
CATS = {"district": "Stadtteil", "zip": "PLZ", "building_type": "Gebäudetyp"}
STOP = ("und der die das mit in im für ein eine einer eines zu zum zur von vom auf ist sind sie es wir ihr ihre "
        "dem den des bei als auch oder nicht nur sehr bis an am aus nach über wohnung dresden").split()


def _warm(r):
    if r.get("price_warm"):
        return r["price_warm"]
    if r.get("price_cold"):
        return r["price_cold"] + (r.get("costs_extra") or 0) + (r.get("costs_heat") or 0)
    return None


def _district(r):
    d = (r.get("district") or "").lower()
    return re.split(r"[/,(]", d)[0].strip() or "?"


def _features(rows):
    num = np.array([[{"warm": _warm(r), "sqm": (r["price_cold"] / r["area"]) if r.get("price_cold") and r.get("area") else None}.get(k, r.get(k))
                     for k in NUMERIC] for r in rows], dtype=float)
    med = np.nanmedian(num, axis=0)
    med = np.where(np.isnan(med), 0, med)
    num = np.where(np.isnan(num), med, num)
    num = StandardScaler().fit_transform(num)
    boo = np.array([[1.0 if r.get(k) == 1 else 0.0 for k in BOOLS] for r in rows])
    cats = [[_district(r), r.get("zip") or "?", r.get("building_type") or "?"] for r in rows]
    enc = OneHotEncoder(handle_unknown="ignore", min_frequency=3)
    cat = enc.fit_transform(cats)
    tfidf = TfidfVectorizer(max_features=3000, min_df=3, stop_words=STOP, token_pattern=r"(?u)\b[^\W\d_]{4,}\b")
    txt = tfidf.fit_transform([f"{r.get('title') or ''} {r.get('description') or ''}" for r in rows])
    cat_names = []
    for n in enc.get_feature_names_out(list(CATS)):
        col = next(c for c in CATS if n.startswith(c + "_"))
        val = n[len(col) + 1:]
        cat_names.append("" if "infrequent" in val or val == "?" else f"{CATS[col]}: {val}")
    names = (list(NUMERIC) + list(BOOLS.values()) + cat_names
             + [f"„{w}“" for w in tfidf.get_feature_names_out()])
    # Text etwas stärker gewichten, damit Stil-Wörter (Altbau, Dielen, Stuck …) durchkommen
    X = sparse.hstack([sparse.csr_matrix(num), sparse.csr_matrix(boo), cat * 1.0, txt * 1.5]).tocsr()
    return X, names


# Schnellgründe aus der Swipe-Ansicht ("Warum nicht?") -> Regel: (Prüfung je Wohnung, abgeleitet aus den abgelehnten)
CHIPS = ["zu teuer", "zu klein", "zu groß", "Lage", "Plattenbau/Block", "Erdgeschoss", "kein Balkon",
         "befristet", "dunkel", "hässlich/alt", "Grundriss", "Anbieter", "nicht mehr online"]
PENALTY = 20  # Abzug je zutreffendem Grund, max. 3 Gründe
# "nicht mehr online" ist kein Geschmacksurteil -> solche Ablehnungen nicht zum Lernen verwenden
GONE_REASON = re.compile(r"nicht mehr online|inaktiv|deaktiviert|offline|vergeben|nicht mehr verfügbar|reservier", re.I)
# Füllwörter aus Freitext-Gründen, die als Regel fast jedes Inserat treffen würden
FILLER = set("direkt gleich etwas wenig viele vielen mehr eher schon immer einfach leider wirklich total ziemlich "
             "nicht keine kein keinen leider wäre hätte haben gibt dort hier diese dieser dieses".split())


def _parse_reason(text: str | None) -> tuple[list[str], list[str]]:
    """'zu teuer, Lage; Gasheizung, kleine Küche' -> (Chips, Freitext-Wörter)"""
    chips_part, _, free = (text or "").partition(";")
    chips = [c.strip() for c in chips_part.split(",") if c.strip() in CHIPS]
    words = [w.lower() for w in re.findall(r"[^\W\d_]{5,}", free) if w.lower() not in STOP and w.lower() not in FILLER]
    return chips, words


def _rules(rows) -> list[tuple[str, callable]]:
    """Leitet aus den Gründen der abgelehnten Wohnungen Prüfregeln für alle anderen ab."""
    by_chip, words = {}, Counter()
    for r in rows:
        if r["rating"] == -1 and r.get("reason"):
            chips, ws = _parse_reason(r["reason"])
            for c in chips:
                by_chip.setdefault(c, []).append(r)
            words.update(set(ws))
    rules = []
    if (rs := [w for w in map(_warm, by_chip.get("zu teuer", [])) if w]):
        lim = min(rs) * 0.95
        rules.append((f"✕ zu teuer (ab {lim:.0f} €)", lambda r, lim=lim: (_warm(r) or 0) >= lim))
    if (rs := [r["area"] for r in by_chip.get("zu klein", []) if r.get("area")]):
        lim = max(rs) * 1.03
        rules.append((f"✕ zu klein (bis {lim:.0f} m²)", lambda r, lim=lim: r.get("area") and r["area"] <= lim))
    if (rs := [r["area"] for r in by_chip.get("zu groß", []) if r.get("area")]):
        lim = min(rs) * 0.97
        rules.append((f"✕ zu groß (ab {lim:.0f} m²)", lambda r, lim=lim: r.get("area") and r["area"] >= lim))
    if (ds := {_district(r) for r in by_chip.get("Lage", [])} - {"?"}):
        rules.append(("✕ Lage", lambda r, ds=ds: _district(r) in ds))
    if "Plattenbau/Block" in by_chip:
        rules.append(("✕ Plattenbau/Block", lambda r: (r.get("plattenbau_score") or 0) >= 35))
    if "Erdgeschoss" in by_chip:
        rules.append(("✕ Erdgeschoss", lambda r: r.get("floor") == 0))
    if "kein Balkon" in by_chip:
        rules.append(("✕ kein Balkon", lambda r: r.get("balcony") != 1 and r.get("terrace") != 1))
    if "befristet" in by_chip:
        rules.append(("✕ befristet", lambda r: r.get("temporary") == 1))
    if (ps := {(r.get("provider") or "").lower() for r in by_chip.get("Anbieter", [])} - {""}):
        rules.append(("✕ Anbieter", lambda r, ps=ps: (r.get("provider") or "").lower() in ps))
    for w in words:  # Freitext: Wörter, die du genannt hast, in anderen Inseraten wiederfinden
        rules.append((f"✕ „{w}“", lambda r, w=w: w in f"{r.get('title') or ''} {r.get('description') or ''}".lower()))
    return rules


def compute() -> dict:
    rows = db.query("active=1 OR rating IS NOT NULL",
                    columns="id,active,rating,reason,user_gone,provider,title,description,district,zip,building_type,price_cold,"
                            "price_warm,costs_extra,costs_heat,area,rooms,floor,year_built,plattenbau_score," + ",".join(BOOLS))
    for r in rows:
        if r["rating"] == -1 and (r.get("user_gone") or GONE_REASON.search(r.get("reason") or "")):
            r["rating"] = None
    likes = [i for i, r in enumerate(rows) if r["rating"] == 1]
    dislikes = [i for i, r in enumerate(rows) if r["rating"] == -1]
    rules = _rules(rows)
    profile = Counter()
    for r in rows:
        if r["rating"] == -1:
            chips, ws = _parse_reason(r.get("reason"))
            profile.update(chips + [f"„{w}“" for w in ws])
    out = {"likes": len(likes), "dislikes": len(dislikes), "mode": "none", "scores": {}, "reasons": {},
           "warnings": {}, "profile": profile.most_common(8)}
    if len(rows) < 10 or not (likes or rules):
        return out
    if not likes:
        # Nur Gründe, noch keine Likes: neutral 50, Regeln ziehen ab
        out["mode"] = "Gründe"
        for r in rows:
            if r["active"]:
                hits = [label for label, test in rules if test(r)][:3]
                out["scores"][r["id"]] = 50 - PENALTY * len(hits)
                out["warnings"][r["id"]] = hits
        return out
    X, names = _features(rows)
    if len(likes) >= 2 and len(dislikes) >= 2:
        idx = likes + dislikes
        y = [1] * len(likes) + [0] * len(dislikes)
        model = LogisticRegression(C=0.5, class_weight="balanced", max_iter=2000).fit(X[idx], y)
        raw = model.predict_proba(X)[:, 1]
        coef = model.coef_[0]
        out["mode"] = "gelernt"
    else:
        # Wenige Bewertungen: Ähnlichkeit zum Durchschnitt der Likes (minus Dislikes)
        coef = np.asarray(X[likes].mean(axis=0)).ravel()
        if dislikes:
            coef = coef - 0.5 * np.asarray(X[dislikes].mean(axis=0)).ravel()
        raw = X @ coef
        out["mode"] = "ähnlich"
    # Auf 0-100 bringen (Rang), damit die Werte über die Zeit vergleichbar lesbar sind
    order = np.argsort(np.argsort(raw))
    pct = (order / max(1, len(raw) - 1) * 100).round().astype(int)
    for i, r in enumerate(rows):
        if not r["active"]:
            continue
        hits = [label for label, test in rules if test(r)][:3]
        out["scores"][r["id"]] = max(0, int(pct[i]) - PENALTY * len(hits))
        out["warnings"][r["id"]] = hits
        xi = X[i].toarray().ravel()
        c = xi * coef
        top = []
        for j in np.argsort(-c)[:5]:
            if c[j] <= 0.02 or not names[j]:
                continue
            top.append(NUMERIC[names[j]][int(xi[j] > 0)] if names[j] in NUMERIC else names[j])
        out["reasons"][r["id"]] = top[:3]
    return out
