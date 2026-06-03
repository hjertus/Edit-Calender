from flask import (Flask, Response, render_template, request,
                   jsonify, session, redirect, url_for)
import requests, json, os, re, uuid, hashlib, secrets
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DATA_FILE   = os.environ.get("DATA_FILE",   "/data/calendars.json")
AUTH_FILE   = os.environ.get("AUTH_FILE",   "/data/auth.json")
PASSWORD    = os.environ.get("APP_PASSWORD", "changeme")


# ── AUTH HELPERS ──────────────────────────────────────────────────────────────

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def check_login():
    return session.get("authed") is True

def require_login(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not check_login():
            if request.path.startswith("/api/") or request.path.startswith("/calendar/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect("/login")
        return fn(*args, **kwargs)
    return wrapper


# ── DATA HELPERS ──────────────────────────────────────────────────────────────

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"calendars": []}

def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_calendar(cal_id):
    data = load_data()
    return next((c for c in data["calendars"] if c["id"] == cal_id), None)


# ── ICS HELPERS ───────────────────────────────────────────────────────────────

def fetch_ics(url):
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text

def parse_events(ics_text):
    events = []
    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", ics_text, re.DOTALL)
    for block in blocks:
        sm = re.search(r"^SUMMARY[^:]*:(.*)", block, re.MULTILINE)
        dm = re.search(r"^DTSTART[^:]*:(.*)", block, re.MULTILINE)
        dm_end = re.search(r"^DTEND[^:]*:(.*)", block, re.MULTILINE)
        summary  = sm.group(1).strip()  if sm  else "(no title)"
        dtstart  = dm.group(1).strip()  if dm  else ""
        dtend    = dm_end.group(1).strip() if dm_end else ""
        events.append({"summary": summary, "dtstart": dtstart, "dtend": dtend})
    return events

def filter_ics(ics_text, hidden_keywords, custom_renames):
    hm = re.match(r"(.*?)(?=BEGIN:VEVENT)", ics_text, re.DOTALL)
    fm = re.search(r"(END:VCALENDAR\s*)$", ics_text, re.DOTALL)
    header = hm.group(1) if hm else "BEGIN:VCALENDAR\r\n"
    footer = fm.group(1) if fm else "END:VCALENDAR\r\n"

    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", ics_text, re.DOTALL)
    kept = []
    for block in blocks:
        sm = re.search(r"^(SUMMARY[^:]*):(.*)", block, re.MULTILINE)
        if not sm:
            kept.append(f"BEGIN:VEVENT{block}END:VEVENT"); continue
        summary = sm.group(2).strip()
        if any(kw.lower() in summary.lower() for kw in hidden_keywords if kw.strip()):
            continue
        new_block = block
        for r in custom_renames:
            old, new = r.get("old",""), r.get("new","")
            if old and new and old.lower() in summary.lower():
                ns = re.sub(re.escape(old), new, summary, flags=re.IGNORECASE)
                new_block = re.sub(
                    r"(SUMMARY[^:]*:).*",
                    lambda m, ns=ns: m.group(1) + ns,
                    new_block, flags=re.MULTILINE)
        kept.append(f"BEGIN:VEVENT{new_block}END:VEVENT")

    return header + "\r\n".join(kept) + "\r\n" + footer


# ── AUTH ROUTES ───────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        pw = request.form.get("password","")
        if hash_pw(pw) == hash_pw(PASSWORD):
            session["authed"] = True
            session.permanent = True
            return redirect("/")
        error = "Wrong password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ── PAGE ROUTES ───────────────────────────────────────────────────────────────

@app.route("/")
@require_login
def index():
    return render_template("index.html")

@app.route("/cal/<cal_id>")
@require_login
def cal_detail(cal_id):
    if not get_calendar(cal_id):
        return redirect("/")
    return render_template("detail.html")


# ── API: CALENDARS ────────────────────────────────────────────────────────────

@app.route("/api/calendars", methods=["GET"])
@require_login
def list_calendars():
    return jsonify(load_data()["calendars"])

@app.route("/api/calendars", methods=["POST"])
@require_login
def create_calendar():
    body = request.json
    url  = body.get("url","").strip()
    name = body.get("name","").strip() or "My Calendar"
    if not url:
        return jsonify({"error": "URL required"}), 400
    cal = {
        "id": str(uuid.uuid4()),
        "name": name, "url": url,
        "hidden_keywords": [], "custom_renames": [],
        "created_at": datetime.utcnow().isoformat()
    }
    data = load_data()
    data["calendars"].append(cal)
    save_data(data)
    return jsonify(cal), 201

@app.route("/api/calendars/<cal_id>", methods=["GET"])
@require_login
def get_cal(cal_id):
    cal = get_calendar(cal_id)
    return jsonify(cal) if cal else (jsonify({"error":"Not found"}), 404)

@app.route("/api/calendars/<cal_id>", methods=["PUT"])
@require_login
def update_cal(cal_id):
    data = load_data()
    for i, c in enumerate(data["calendars"]):
        if c["id"] == cal_id:
            body = request.json
            data["calendars"][i].update({
                "name":            body.get("name",            c["name"]),
                "url":             body.get("url",             c["url"]),
                "hidden_keywords": body.get("hidden_keywords", c["hidden_keywords"]),
                "custom_renames":  body.get("custom_renames",  c["custom_renames"]),
            })
            save_data(data)
            return jsonify(data["calendars"][i])
    return jsonify({"error":"Not found"}), 404

@app.route("/api/calendars/<cal_id>", methods=["DELETE"])
@require_login
def delete_cal(cal_id):
    data = load_data()
    data["calendars"] = [c for c in data["calendars"] if c["id"] != cal_id]
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/calendars/<cal_id>/preview")
@require_login
def preview_cal(cal_id):
    cal = get_calendar(cal_id)
    if not cal:
        return jsonify({"error":"Not found"}), 404
    try:
        ics    = fetch_ics(cal["url"])
        events = parse_events(ics)
        for e in events:
            e["hidden"] = any(
                kw.lower() in e["summary"].lower()
                for kw in cal["hidden_keywords"] if kw.strip()
            )
        return jsonify({"events": events})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


# ── ICS SERVE (public — Apple Calendar needs no session) ─────────────────────
# Protected by the unguessable UUID, which is sufficient for calendar feeds.

@app.route("/calendar/<cal_id>.ics")
def serve_ics(cal_id):
    cal = get_calendar(cal_id)
    if not cal:
        return Response("Not found", status=404, mimetype="text/plain")
    try:
        ics      = fetch_ics(cal["url"])
        filtered = filter_ics(ics, cal["hidden_keywords"], cal["custom_renames"])
        return Response(filtered, mimetype="text/calendar",
                        headers={"Content-Disposition": f"inline; filename={cal_id}.ics"})
    except Exception as ex:
        return Response(f"Error: {ex}", status=500, mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8742, debug=False)