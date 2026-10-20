"""Watchdog backend: watches -> TinyFish agents -> stored JSON -> diff -> feed + digest."""
import json, os, sqlite3, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from tinyfish import run_agent

load_dotenv()
ROOT = Path(__file__).resolve().parent
DB = os.getenv("DB_PATH", str(ROOT / "watchdog.db"))
app = FastAPI(title="Watchdog")

SHAPE = ' Return ONLY JSON of the form {"items": [ {...}, ... ]}, one object per result found.'
TEMPLATES = {
    "job": ("Job Tracker", "List the open job postings on this page. Each item: title, location, link."),
    "internship": ("Internship Tracker", "List the internships on this page. Each item: title, company, location, deadline, link."),
    "price": ("Price Hunter", "Get the product's current price and availability. Each item: name, price (number), currency, in_stock (true/false)."),
    "scholarship": ("Scholarship Finder", "List the scholarships on this page. Each item: name, amount, deadline, eligibility, link."),
    "custom": ("Custom", ""),
}
FREQ = {"hourly": 3600, "daily": 86400, "manual": None}


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


with db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS watches(id INTEGER PRIMARY KEY, name TEXT, url TEXT, template TEXT, goal TEXT, freq TEXT, last_run REAL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS results(id INTEGER PRIMARY KEY, watch_id INTEGER, ts REAL, ok INTEGER, items TEXT, changes TEXT, error TEXT);
    """)


def normalize(res):
    """Turn whatever the agent returned into a list of dict items."""
    fix = lambda L: [i if isinstance(i, dict) else {"value": i} for i in L]
    if isinstance(res, list):
        return fix(res)
    if isinstance(res, dict):
        for v in [res.get("items")] + list(res.values()):
            if isinstance(v, list):
                return fix(v)
        return [res]
    return []


def item_key(i):
    for k in ("link", "url", "name", "title"):
        if i.get(k):
            return str(i[k])
    return json.dumps(i, sort_keys=True)


def diff(old, new):
    """Items that are new, or whose fields changed, since the last check."""
    prev, out = {item_key(i): i for i in old}, []
    for i in new:
        o = prev.get(item_key(i))
        if o is None:
            out.append({"type": "new", "item": i})
        else:
            d = {f: [o.get(f), v] for f, v in i.items() if o.get(f) != v}
            if d:
                out.append({"type": "changed", "item": i, "diff": d})
    return out


def build_goal(w):
    base = TEMPLATES.get(w["template"], TEMPLATES["custom"])[1]
    return f'{base} {w["goal"] or ""}'.strip() + SHAPE


def check(w):
    items, err = None, None
    try:
        items = normalize(run_agent(w["url"], build_goal(w)))
    except Exception as e:
        err = str(e)
    with db() as c:
        prev = c.execute("SELECT items FROM results WHERE watch_id=? AND ok=1 ORDER BY id DESC LIMIT 1", (w["id"],)).fetchone()
        changes = diff(json.loads(prev["items"]), items) if prev and items is not None else []
        c.execute("INSERT INTO results(watch_id,ts,ok,items,changes,error) VALUES(?,?,?,?,?,?)",
                  (w["id"], time.time(), int(err is None), json.dumps(items), json.dumps(changes), err))
        c.execute("UPDATE watches SET last_run=? WHERE id=?", (time.time(), w["id"]))


class WatchIn(BaseModel):
    name: str
    url: str
    template: str = "custom"
    goal: str = ""
    freq: str = "manual"


@app.get("/")
def index():
    return FileResponse(ROOT.parent / "frontend" / "index.html")


@app.get("/api/templates")
def templates():
    return {k: v[0] for k, v in TEMPLATES.items()}


@app.post("/api/watches")
def add_watch(w: WatchIn):
    if w.template not in TEMPLATES or w.freq not in FREQ:
        raise HTTPException(400, "Unknown template or frequency")
    if w.template == "custom" and not w.goal.strip():
        raise HTTPException(400, "Custom watches need a goal")
    with db() as c:
        cur = c.execute("INSERT INTO watches(name,url,template,goal,freq) VALUES(?,?,?,?,?)",
                        (w.name, w.url, w.template, w.goal, w.freq))
    return {"id": cur.lastrowid}


@app.delete("/api/watches/{wid}")
def delete_watch(wid: int):
    with db() as c:
        c.execute("DELETE FROM results WHERE watch_id=?", (wid,))
        c.execute("DELETE FROM watches WHERE id=?", (wid,))
    return {"ok": True}


@app.get("/api/watches")
def feed():
    out = []
    with db() as c:
        for w in c.execute("SELECT * FROM watches ORDER BY id DESC").fetchall():
            r = c.execute("SELECT * FROM results WHERE watch_id=? ORDER BY id DESC LIMIT 1", (w["id"],)).fetchone()
            d = dict(w)
            d["latest"] = {"ts": r["ts"], "ok": bool(r["ok"]), "items": json.loads(r["items"] or "null"),
                           "changes": json.loads(r["changes"]), "error": r["error"]} if r else None
            out.append(d)
    return out


@app.post("/api/check")
def check_all(watch_id: int | None = None):
    with db() as c:
        q = "SELECT * FROM watches" + (" WHERE id=?" if watch_id else "")
        ws = [dict(r) for r in c.execute(q, (watch_id,) if watch_id else ())]
    with ThreadPoolExecutor(max_workers=int(os.getenv("MAX_PARALLEL", "5"))) as ex:  # agents run in parallel
        list(ex.map(check, ws))
    return {"checked": len(ws)}


@app.post("/api/digest")
def digest():
    lines = [f'{w["name"]}: {c["type"]} {json.dumps(c.get("diff") or c["item"])[:200]}'
             for w in feed() if w["latest"] for c in w["latest"]["changes"]]
    if not lines:
        return {"digest": "No changes since the last check."}
    akey = os.getenv("ANTHROPIC_API_KEY")
    if not akey:
        return {"digest": f"{len(lines)} change(s): " + "; ".join(lines[:4])}
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=60,
                      headers={"x-api-key": akey, "anthropic-version": "2023-06-01"},
                      json={"model": os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"), "max_tokens": 200,
                            "messages": [{"role": "user", "content":
                                          "Write a friendly one or two sentence digest of these web changes:\n" + "\n".join(lines)}]})
    r.raise_for_status()
    return {"digest": r.json()["content"][0]["text"]}


def scheduler():
    while True:
        time.sleep(60)
        with db() as c:
            due = [dict(w) for w in c.execute("SELECT * FROM watches").fetchall()
                   if FREQ.get(w["freq"]) and time.time() - w["last_run"] >= FREQ[w["freq"]]]
        for w in due:
            check(w)


if os.getenv("AUTO_CHECK") == "1":
    threading.Thread(target=scheduler, daemon=True).start()
