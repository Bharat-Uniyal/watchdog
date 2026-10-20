"""Thin client for the TinyFish Agent API (POST /v1/automation/run)."""
import os, random, requests

URL = "https://agent.tinyfish.ai/v1/automation/run"


def _mock(url):
    return {"items": [
        {"name": "Demo Headphones", "price": random.choice([9999, 10499, 8999]), "currency": "INR", "in_stock": True},
        {"title": "Software Intern", "location": "Remote", "link": url + "#1"},
        {"title": random.choice(["Data Intern", "ML Intern"]), "location": "Delhi", "link": url + "#" + str(random.randint(2, 3))},
    ]}


def run_agent(url, goal):
    """Run one agent on `url` with a plain-English `goal`; return its structured result."""
    if os.getenv("MOCK") == "1":
        return _mock(url)
    key = os.getenv("TINYFISH_API_KEY")
    if not key:
        raise RuntimeError("TINYFISH_API_KEY is not set (or set MOCK=1 for demo data)")
    r = requests.post(
        URL,
        headers={"X-API-Key": key},
        json={"url": url, "goal": goal, "browser_profile": os.getenv("TINYFISH_PROFILE", "lite")},
        timeout=int(os.getenv("TINYFISH_TIMEOUT", "300")),
    )
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"TinyFish returned HTTP {r.status_code} with a non-JSON body")
    err = data.get("error")
    if r.status_code >= 400 or data.get("status") == "FAILED":
        raise RuntimeError((err or {}).get("message") or f"TinyFish error (HTTP {r.status_code})")
    return data.get("result")
