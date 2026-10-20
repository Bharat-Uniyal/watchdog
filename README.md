# Watchdog

**One tracker for anything on the web.** Tell Watchdog what to watch in plain English; [TinyFish](https://docs.tinyfish.ai) web agents visit the page, extract structured JSON, and Watchdog flags what changed since the last check.

Job tracker, internship tracker, price hunter and scholarship finder are all the same engine: a URL, a goal, and a diff.

## How it works

1. Add a **watch**: URL + template (or custom goal) + frequency.
2. Watchdog calls the TinyFish Agent API (`POST /v1/automation/run`, `X-API-Key` header) with the URL and goal. Watches run in parallel.
3. The `result` JSON is normalised to a list of items and stored in SQLite.
4. Each new result is diffed against the previous one: **new** items and **changed** fields (e.g. `price: 9999 → 8999`).
5. The dashboard shows one feed; **AI digest** summarises the day's changes with Claude (optional).

```
frontend/index.html -> FastAPI (backend/app.py) -> TinyFish Agent API (backend/tinyfish.py)
                              |-> SQLite -> diff -> feed + digest
```

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add TINYFISH_API_KEY
cd backend && uvicorn app:app --reload
```

Open http://127.0.0.1:8000. No key yet? Set `MOCK=1` in `.env` to see the whole flow with fake data (results change between checks, so alerts fire).

## Add a template

Edit `TEMPLATES` in `backend/app.py`: a label and a goal sentence listing the fields you want. That's it.

## Demo script (3 min)

1. Empty dashboard. 2. Add three watches (a careers page, a product page, a scholarship page). 3. **Check all now** while agents run. 4. Show the unified feed; check again to show a change alert. 5. Click **AI digest**.

## Notes

- Pre-test your demo sites. Agents can be slow or fail on unusual pages; failures show as a card, not a crash.
- Sites that block basic automation: set `TINYFISH_PROFILE=stealth`.
- Respect each site's terms of service.
- Changes are matched per item using `link`, `url`, `name`, or `title`.
- Ideas next: webhooks/email alerts, TinyFish Search API to discover pages, live run streaming (`/run-sse`).
