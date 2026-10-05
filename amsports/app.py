"""Am'Sports – serveur de paiement (Flask + API HelloAsso Checkout).

- Lit la liste des personnes (CSV local ou Google Sheet publié en CSV)
- Crée un paiement HelloAsso au montant exact de la liste (montant décidé côté serveur)
- Marque "payé" après VÉRIFICATION auprès de HelloAsso (jamais sur la seule parole du navigateur)
"""
import csv
import io
import os
import sqlite3
import time
import unicodedata
from contextlib import closing

import requests
from flask import Flask, jsonify, redirect, request

app = Flask(__name__, static_folder="static", static_url_path="")

# ---------- Config (variables d'environnement) ----------
HA_BASE = os.environ.get("HELLOASSO_API_BASE", "https://api.helloasso.com")  # sandbox: https://api.helloasso-sandbox.com
HA_CLIENT_ID = os.environ.get("HELLOASSO_CLIENT_ID", "")
HA_CLIENT_SECRET = os.environ.get("HELLOASSO_CLIENT_SECRET", "")
HA_ORG_SLUG = os.environ.get("HELLOASSO_ORG_SLUG", "")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")  # URL publique du site (https obligatoire en prod)
SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "")  # Google Sheet > Fichier > Partager > Publier sur le web > CSV
DB_PATH = os.environ.get("DB_PATH", "data.db")  # sur Render : /data/data.db avec un disque persistant
PEOPLE_FILE = os.environ.get("PEOPLE_FILE", "people.csv")

# ---------- Base SQLite (statuts de paiement) ----------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    with closing(db()) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS intents(
                intent_id TEXT PRIMARY KEY, ref TEXT NOT NULL,
                created REAL NOT NULL, paid INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS paid(ref TEXT PRIMARY KEY, paid_at REAL NOT NULL, intent_id TEXT);
            """
        )
        con.commit()


init_db()

# ---------- Liste des personnes ----------
_cache = {"t": 0.0, "rows": []}


def _norm(s):
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()


def _amount(v):
    return float(str(v).replace("€", "").replace("\u202f", "").replace(" ", "").replace(",", "."))


def load_people():
    if time.time() - _cache["t"] < 60 and _cache["rows"]:
        return _cache["rows"]
    if SHEET_CSV_URL:
        r = requests.get(SHEET_CSV_URL, timeout=10)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig")
    else:
        with open(PEOPLE_FILE, encoding="utf-8-sig") as f:
            text = f.read()
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        row = {_norm(k): (v or "").strip() for k, v in row.items() if k}
        if not row.get("ref") or not row.get("nom"):
            continue
        try:
            amt = _amount(row.get("montant", "0"))
        except ValueError:
            continue
        rows.append(
            {
                "ref": row["ref"],
                "name": row["nom"],
                "amount": amt,
                "paid_in_sheet": _norm(row.get("paye", "")) in ("oui", "1", "true", "x", "paye"),
            }
        )
    _cache.update(t=time.time(), rows=rows)
    return rows


def paid_refs():
    with closing(db()) as con:
        return {r["ref"] for r in con.execute("SELECT ref FROM paid")}


def find_person(ref):
    for p in load_people():
        if p["ref"] == ref:
            return p
    return None


# ---------- HelloAsso ----------
_token = {"v": None, "exp": 0.0}


def ha_token():
    if _token["v"] and time.time() < _token["exp"] - 60:
        return _token["v"]
    r = requests.post(
        f"{HA_BASE}/oauth2/token",
        data={"grant_type": "client_credentials", "client_id": HA_CLIENT_ID, "client_secret": HA_CLIENT_SECRET},
        timeout=10,
    )
    r.raise_for_status()
    j = r.json()
    _token.update(v=j["access_token"], exp=time.time() + int(j.get("expires_in", 1800)))
    return _token["v"]


def ha_headers():
    return {"Authorization": f"Bearer {ha_token()}", "Content-Type": "application/json"}


def verify_intent(intent_id):
    """Demande à HelloAsso si ce paiement a abouti. Marque 'payé' seulement si une commande existe."""
    r = requests.get(
        f"{HA_BASE}/v5/organizations/{HA_ORG_SLUG}/checkout-intents/{intent_id}",
        headers=ha_headers(),
        timeout=10,
    )
    if r.status_code != 200:
        return False
    if not r.json().get("order"):
        return False
    with closing(db()) as con:
        row = con.execute("SELECT ref FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
        if not row:
            return False
        con.execute("UPDATE intents SET paid=1 WHERE intent_id=?", (intent_id,))
        con.execute(
            "INSERT OR IGNORE INTO paid(ref, paid_at, intent_id) VALUES(?,?,?)",
            (row["ref"], time.time(), intent_id),
        )
        con.commit()
    return True


# ---------- Routes ----------
@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/search")
def search():
    q = _norm(request.args.get("q", ""))
    if len(q) < 2:  # évite de lister tout le monde d'un coup
        return jsonify([])
    done = paid_refs()
    out = []
    for p in load_people():
        if q in _norm(p["name"]):
            out.append(
                {
                    "ref": p["ref"],
                    "name": p["name"],
                    "amount": p["amount"],
                    "paid": p["paid_in_sheet"] or p["ref"] in done,
                }
            )
        if len(out) >= 20:
            break
    return jsonify(out)


@app.post("/api/pay")
def pay():
    ref = (request.get_json(silent=True) or {}).get("ref", "")
    p = find_person(ref)
    if not p:
        return jsonify(error="Personne introuvable"), 404
    if p["paid_in_sheet"] or ref in paid_refs():
        return jsonify(error="Déjà payé"), 409
    cents = round(p["amount"] * 100)  # montant décidé ICI, jamais par le navigateur
    if cents <= 0:
        return jsonify(error="Rien à payer"), 400
    body = {
        "totalAmount": cents,
        "initialAmount": cents,
        "itemName": f"Am'Sports - {p['name']}"[:250],
        "backUrl": BASE_URL,
        "errorUrl": f"{BASE_URL}/retour?type=error",
        "returnUrl": f"{BASE_URL}/retour?type=return",
        "containsDonation": False,
        "metadata": {"ref": ref},
    }
    try:
        r = requests.post(
            f"{HA_BASE}/v5/organizations/{HA_ORG_SLUG}/checkout-intents",
            json=body,
            headers=ha_headers(),
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        app.logger.error("HelloAsso error: %s %s", e, getattr(e.response, "text", ""))
        return jsonify(error="Le paiement est momentanément indisponible"), 502
    with closing(db()) as con:
        con.execute("INSERT OR REPLACE INTO intents(intent_id, ref, created) VALUES(?,?,?)", (str(j["id"]), ref, time.time()))
        con.commit()
    return jsonify(url=j["redirectUrl"])


@app.get("/retour")
def retour():
    """Retour du client depuis HelloAsso : on vérifie auprès de HelloAsso, on ne fait pas confiance à l'URL."""
    intent_id = request.args.get("checkoutIntentId", "")
    if request.args.get("type") == "error":
        return redirect("/?statut=erreur")
    try:
        ok = bool(intent_id) and verify_intent(intent_id)
    except requests.RequestException:
        ok = False
    return redirect("/?statut=ok" if ok else "/?statut=attente")


@app.post("/webhook/helloasso")
def webhook():
    """Notification HelloAsso : on revérifie les paiements récents en attente (le contenu reçu n'est pas cru)."""
    with closing(db()) as con:
        pending = [r["intent_id"] for r in con.execute(
            "SELECT intent_id FROM intents WHERE paid=0 AND created>?", (time.time() - 86400,))]
    for iid in pending[:50]:
        try:
            verify_intent(iid)
        except requests.RequestException:
            pass
    return "", 200


@app.get("/healthz")
def healthz():
    return "ok"
