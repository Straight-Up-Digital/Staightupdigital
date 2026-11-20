"""One-off helper: turns a Zoho Self Client grant code into a refresh token.
Run it, answer the prompts, copy the refresh token it prints into ZOHO_REFRESH_TOKEN. Nothing is saved to disk."""
import getpass
import httpx

dc = input("Zoho data centre (com, eu, in, com.au) [com]: ").strip() or "com"
client_id = input("Client ID: ").strip()
client_secret = getpass.getpass("Client secret (hidden): ").strip()
code = getpass.getpass("Grant code (hidden, valid for about 10 minutes): ").strip()
r = httpx.post(f"https://accounts.zoho.{dc}/oauth/v2/token", timeout=30, data={
    "grant_type": "authorization_code", "client_id": client_id, "client_secret": client_secret, "code": code})
data = r.json()
if data.get("refresh_token"):
    print("\nYour refresh token (keep it private):\n" + data["refresh_token"])
else:
    print("\nZoho said:", data)
