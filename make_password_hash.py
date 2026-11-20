"""One-off helper: makes a password hash for CRM_PASSWORD_HASH. Nothing is saved to disk."""
import base64, getpass, hashlib, os

pw = getpass.getpass("Choose a CRM password (hidden, 12+ characters): ")
if len(pw) < 12 or pw != getpass.getpass("Type it again: "):
    raise SystemExit("Too short, or the two did not match. Run it again.")
salt = os.urandom(16)
iters = 300000
dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iters)
print("\nYour hash. Use it as CRM_PASSWORD_HASH (it is not the password, but keep it private):\n" +
      f"pbkdf2_sha256${iters}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}")
