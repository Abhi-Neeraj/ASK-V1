"""Deploy the latest commit of the `singapore-free` branch to the Render server and wait until it is live.

Render's automatic deploys did not start when commits were pushed (no deploy appeared after the first one), so this
starts one by hand, the same as pressing "Manual Deploy" in the dashboard.

    python platform/deploy/render_deploy.py

It asks for ONE thing, hidden as you type: a Render API key (Account Settings > API Keys). Nothing secret is printed.
"""

from __future__ import annotations

import getpass
import json
import time
import urllib.error
import urllib.request

API = "https://api.render.com/v1"
SERVICE = "srv-dbu99e0n74is73d9dk1g"
URL = "https://ask-api-uaq9.onrender.com"


def call(token: str, method: str, path: str, body: dict | None = None):
    request = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read().decode()
            return json.loads(text) if text else None
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Render answered {error.code} to {method} {path}: {error.read().decode(errors='replace')[:300]}") from None


def main() -> int:
    token = getpass.getpass("Render API key (hidden): ").strip()
    service = call(token, "GET", f"/services/{SERVICE}")
    print("auto deploy setting:", service.get("autoDeployTrigger") or service.get("autoDeploy"), "| branch:", service.get("branch"))
    # Recordings are kept now (a compressed copy in the database), so the old "delete the audio after processing" is off.
    call(token, "PUT", f"/services/{SERVICE}/env-vars/ASK_DELETE_AUDIO_AFTER_PROCESSING", {"value": "false"})
    print("setting saved: ASK_DELETE_AUDIO_AFTER_PROCESSING=false")
    deploy = call(token, "POST", f"/services/{SERVICE}/deploys", {"clearCache": "do_not_clear"})
    deploy_id = deploy["id"]
    commit = (deploy.get("commit") or {})
    print("deploy started:", deploy_id, "| commit:", (commit.get("id") or "")[:8], (commit.get("message") or "")[:70])
    last = ""
    for _ in range(150):
        current = call(token, "GET", f"/services/{SERVICE}/deploys/{deploy_id}")
        status = current.get("status", "?")
        if status != last:
            print("  status:", status, "|", time.strftime("%H:%M:%S"))
            last = status
        if status in {"live", "build_failed", "update_failed", "canceled", "deactivated", "pre_deploy_failed"}:
            break
        time.sleep(10)
    print("address:", URL)
    return 0 if last == "live" else 1


if __name__ == "__main__":
    raise SystemExit(main())
