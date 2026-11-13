"""Checks every place a SerpApi key might be stored. Never prints a full key.
Run from the business_auditor folder:  .venv\\Scripts\\python.exe check_key.py
The account endpoint is free and does not use search quota."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def mask(key: str) -> str:
    return f"{key[:4]}...{key[-2:]} (length {len(key)})" if len(key) > 8 else f"(length {len(key)})"


def test(label: str, key: str) -> None:
    key = key.strip().strip('"').strip("'")
    if not key:
        print(f"{label}: empty")
        return
    url = "https://serpapi.com/account.json?" + urllib.parse.urlencode({"api_key": key})
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        left = data.get("total_searches_left", data.get("plan_searches_left", "?"))
        print(f"{label}: {mask(key)} -> ACCEPTED, searches left: {left}")
    except urllib.error.HTTPError as e:
        print(f"{label}: {mask(key)} -> REFUSED (HTTP {e.code})")
    except Exception as e:
        print(f"{label}: {mask(key)} -> could not test ({type(e).__name__}: {e})")


print("--- 1. Windows environment variable SERPAPI_API_KEY ---")
test("environment variable", os.environ.get("SERPAPI_API_KEY", ""))

print("\n--- 2. The .env file ---")
env_path = HERE / ".env"
if not env_path.is_file():
    print(".env not found in this folder")
else:
    for n, line in enumerate(env_path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            print(f"line {n}: NO VARIABLE NAME, just a value of length {len(line)}. This is the problem if it is the key.")
            test(f"line {n} as a key", line)
            continue
        name, value = line.split("=", 1)
        print(f"line {n}: name = {name.strip()!r}")
        if "SERP" in name.upper():
            test(f"line {n} value", value)

print("\n--- 3. Serp Api Key.txt ---")
txt = HERE / "Serp Api Key.txt"
if txt.is_file():
    test("key file", txt.read_text(encoding="utf-8-sig"))
else:
    print("file not found")
