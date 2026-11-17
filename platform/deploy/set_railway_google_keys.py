"""Set the two Google keys on the Railway `api` service without showing them or putting them anywhere else.

Run it yourself in a terminal (it asks for the keys with hidden input, so nothing is echoed, logged or passed on a
command line; the keys go to Railway over standard input):

    python platform/deploy/set_railway_google_keys.py

It needs the Railway CLI logged in and linked to the project (`npx -y @railway/cli@latest login`, then `link` from
`platform/api`). Railway restarts the API when the last key is set; the processing worker starts by itself.

  ASK_GOOGLE_STT_API_KEY  a Google Cloud API key with the Speech-to-Text API enabled (speech to text)
  ASK_GEMINI_API_KEY      a Gemini API key (the summary); the same key works if it is allowed to use both APIs
"""

import getpass
import re
import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent / "api"
RAILWAY = "npx -y @railway/cli@latest"


REPEATED_KEY = re.compile(r"^(AIza[0-9A-Za-z_-]{35})\1+$")


def ask(prompt: str) -> str:
    value = getpass.getpass(prompt).strip().strip('"').strip("'")
    # A hidden prompt can take one paste several times over: a key pasted twice or more is stored once.
    repeated = REPEATED_KEY.match(value)
    if repeated:
        print("That was the same key pasted more than once: using it once.")
        return repeated.group(1)
    return value


def set_variable(name: str, value: str, *, restart: bool) -> None:
    command = f"{RAILWAY} variable set {name} --stdin --service api" + ("" if restart else " --skip-deploys")
    result = subprocess.run(command, input=value, text=True, capture_output=True, cwd=API_DIR, shell=True)
    if result.returncode != 0:
        # The message names the variable only; the value is never printed.
        print(f"Could not set {name}: {result.stderr.strip()[-300:]}")
        sys.exit(1)
    print(f"Set {name}" + (" (the API restarts now)" if restart else ""))


def main() -> None:
    print("The keys are typed hidden: nothing shows while you paste. Press Enter after each one.\n")
    stt = ask("Google key for speech-to-text: ")
    gemini = ask("Gemini key for the summary (just Enter to use the same key): ") or stt
    for label, value in (("speech-to-text", stt), ("Gemini", gemini)):
        if not value or any(ch.isspace() for ch in value):
            print(f"The {label} key is empty or contains spaces: copy it again in one piece.")
            sys.exit(1)
        if not value.startswith("AIza"):
            print(f"Note: the {label} key does not start with AIza, which Google API keys usually do. Setting it anyway.")
    set_variable("ASK_GOOGLE_STT_API_KEY", stt, restart=False)
    set_variable("ASK_GEMINI_API_KEY", gemini, restart=True)
    print("\nDone. In about a minute the worker starts and processes the recordings waiting on the server.")


if __name__ == "__main__":
    main()
