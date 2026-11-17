"""Create the ASK server on Render (free plan, Singapore) from the private GitHub repo, with every secret taken from
files on this computer, and wait until it is live.

Run it from the repository root:

    python platform/deploy/render_create_service.py

It asks for ONE thing, hidden as you type: a Render API key (Render dashboard > Account Settings > API Keys > Create).
Nothing secret is printed or written anywhere else. The values it sends come from:

  * platform/data/backup-<date>/api-settings.env   the keys the Railway server used (so locket secrets and sign-ins
                                                   keep working): ASK_SECRET_KEY, ASK_LOCKET_MASTER_KEY and the two
                                                   Google keys
  * platform/api/.env.local                        the Neon connection string (DATABASE_URL_UNPOOLED)
  * render.yaml                                    every value that is not a secret

Render must be able to read the GitHub repo: sign in to Render with GitHub and allow the repo (Account Settings >
GitHub). Safe to run again: if a service called ask-api already exists it only reports it.
"""

from __future__ import annotations

import getpass
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
API = "https://api.render.com/v1"
REPO = "https://github.com/Abhi-Neeraj/ASK-V1"
BRANCH = "singapore-free"
NAME = "ask-api"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


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
        detail = error.read().decode(errors="replace")[:400]
        raise SystemExit(f"Render answered {error.code} to {method} {path}: {detail}") from None


def main() -> int:
    backups = sorted((ROOT / "platform" / "data").glob("backup-*/api-settings.env"))
    if not backups:
        return print("No saved settings found under platform/data/backup-*/") or 1
    saved = read_env(backups[-1])
    neon = read_env(ROOT / "platform" / "api" / ".env.local")
    secrets = {
        "ASK_SECRET_KEY": saved.get("ASK_SECRET_KEY", ""),
        "ASK_LOCKET_MASTER_KEY": saved.get("ASK_LOCKET_MASTER_KEY", ""),
        "ASK_GOOGLE_STT_API_KEY": saved.get("ASK_GOOGLE_STT_API_KEY", ""),
        "ASK_GEMINI_API_KEY": saved.get("ASK_GEMINI_API_KEY", ""),
        "ASK_DATABASE_URL": neon.get("DATABASE_URL_UNPOOLED") or neon.get("DATABASE_URL", ""),
    }
    missing = [k for k, v in secrets.items() if not v]
    if missing:
        return print("Missing values on this computer: " + ", ".join(missing)) or 1

    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    [service] = [s for s in blueprint["services"] if s["name"] == NAME]
    env_vars = []
    for item in service["envVars"]:
        key = item["key"]
        if item.get("sync") is False:
            if key not in secrets:
                return print(f"render.yaml asks for {key}, which this script has no value for") or 1
            env_vars.append({"key": key, "value": secrets[key]})
        elif "value" in item:
            env_vars.append({"key": key, "value": str(item["value"])})
        else:
            return print(f"render.yaml sets {key} in a way this script does not handle") or 1

    token = getpass.getpass("Render API key (hidden): ").strip()
    if not token:
        return print("No key entered.") or 1

    owners = call(token, "GET", "/owners?limit=20") or []
    if not owners:
        return print("This key sees no Render workspace.") or 1
    owner = owners[0]["owner"]
    print(f"Workspace: {owner.get('name')} ({owner.get('type')})")

    existing = [s["service"] for s in (call(token, "GET", f"/services?name={NAME}&limit=20") or []) if s["service"]["name"] == NAME]
    if existing:
        svc = existing[0]
        print(f"A service called {NAME} already exists: {svc.get('serviceDetails', {}).get('url')} (nothing changed)")
        return 0

    created = call(
        token,
        "POST",
        "/services",
        {
            "type": "web_service",
            "name": NAME,
            "ownerId": owner["id"],
            "repo": REPO,
            "branch": BRANCH,
            "autoDeployTrigger": "commit",
            "envVars": env_vars,
            "serviceDetails": {
                "runtime": "docker",
                "plan": "free",
                "region": "singapore",
                "healthCheckPath": service.get("healthCheckPath", "/api/health"),
                "envSpecificDetails": {
                    "dockerfilePath": service["dockerfilePath"],
                    "dockerContext": service["dockerContext"],
                },
            },
        },
    )
    svc = created["service"]
    url = svc["serviceDetails"]["url"]
    deploy_id = created.get("deployId")
    print(f"Created {NAME}: {url}")
    print(f"Dashboard: {svc.get('dashboardUrl')}")

    # Wait for the first build and start (a free instance builds slowly: usually 5 to 10 minutes).
    last = ""
    for _ in range(120):
        deploy = call(token, "GET", f"/services/{svc['id']}/deploys/{deploy_id}") if deploy_id else {}
        status = deploy.get("status", "?")
        if status != last:
            print("  deploy:", status)
            last = status
        if status in {"live", "build_failed", "update_failed", "canceled", "deactivated", "pre_deploy_failed"}:
            break
        time.sleep(10)
    print("Service address:", url)
    return 0 if last == "live" else 1


if __name__ == "__main__":
    sys.exit(main())
