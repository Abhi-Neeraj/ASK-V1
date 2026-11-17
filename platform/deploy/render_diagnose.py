"""Read-only look at the Render server: its recent events (restarts, memory kills, deploys) and the warnings and
errors in its log. Asks for ONE thing, hidden as you type: a Render API key. Prints nothing secret.

    python platform/deploy/render_diagnose.py
"""

from __future__ import annotations

import getpass
import json
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.render.com/v1"
SERVICE = "srv-dbu99e0n74is73d9dk1g"
INTERESTING = re.compile(
    r"error|traceback|exception|warning|memory|oom|killed|restart|took back|failed|timeout|too many|429|5\d\d ",
    re.IGNORECASE,
)


def call(token: str, path: str):
    request = urllib.request.Request(
        API + path, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode() or "null")
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Render answered {error.code} to {path}: {error.read().decode(errors='replace')[:300]}") from None


def scrub(text: str) -> str:
    text = re.sub(r"(postgres(ql)?(\+\w+)?://)[^\s]+", r"\1<hidden>", text)
    text = re.sub(r"(key|token|secret|password)=\S+", r"\1=<hidden>", text, flags=re.IGNORECASE)
    return text[:260]


def main() -> int:
    token = getpass.getpass("Render API key (hidden): ").strip()
    service = call(token, f"/services/{SERVICE}")
    print("service:", service.get("name"), "| plan:", service.get("serviceDetails", {}).get("plan"), "| suspended:", service.get("suspended"))
    print("\nrecent events:")
    for item in call(token, f"/services/{SERVICE}/events?limit=15"):
        event = item.get("event", item)
        detail = json.dumps(event.get("details", {}))[:200]
        print(" ", event.get("timestamp"), event.get("type"), scrub(detail))
    print("\nrecent deploys:")
    for item in call(token, f"/services/{SERVICE}/deploys?limit=5"):
        deploy = item.get("deploy", item)
        print(" ", deploy.get("createdAt"), deploy.get("status"), (deploy.get("commit") or {}).get("message", "")[:60])
    owner = service["ownerId"]
    query = urllib.parse.urlencode(
        {"ownerId": owner, "resource": SERVICE, "limit": 400, "direction": "backward"}
    )
    logs = call(token, f"/logs?{query}").get("logs", [])
    print(f"\nlog lines read: {len(logs)}; the warnings and errors among them (newest first):")
    shown = 0
    for line in logs:
        message = line.get("message", "")
        if INTERESTING.search(message):
            print(" ", line.get("timestamp", "")[:19], scrub(message))
            shown += 1
            if shown >= 40:
                break
    if not shown:
        print("  none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
