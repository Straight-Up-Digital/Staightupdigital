# Straight Up Digital CRM upgrade: final setup

## What is already implemented

The local CRM now includes the requested activity timeline and draft messages; quote, invoice, payment and renewal tracking; project status; audit queue/retry and bounded town batches; duplicate-reviewed CSV import; score history and monthly reporting; local backups; source/consent/opt-out tracking; guarded erasure; and owner-clicked website-enquiry sync. The Python CRM remains bound to `127.0.0.1`; do not expose it through a tunnel.

The public website keeps its existing Zoho email-delivery path. After a successful email send, the production function stores the enquiry in a private Netlify Blob queue. A separate authenticated production-only function returns those records only to the owner’s local CRM. The local CRM acknowledges (deletes) each remote item only after the SQLite import commits. The remote copy is not publicly readable, and the local sync does not grant marketing permission.

## One required Netlify setting

The Netlify CLI is not installed or authenticated on this computer, so the production sync secret could not be added from this session. Also, the latest website source has not yet been deployed; until it is deployed, the private sync endpoint will not exist in production.

When the Netlify deployment is available:

1. In the `straight-up-digital-site` Netlify site, add an environment variable named `CRM_ENQUIRY_SYNC_TOKEN`. Restrict it to **Functions** scope and **Production** context only. Treat its value as a password. Do not add it to Git, a public build variable, chat, email, or this file.
2. Generate a strong random token on the local computer, for example with Python: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Use the same value for the Netlify variable and `CRM_ENQUIRY_SYNC_TOKEN` in the ignored local `C:\Users\Saint\business_auditor\.env` file. Do not send the value to anyone or commit `.env`.
3. Set `CRM_ENQUIRY_SYNC_URL` in that same local `.env` file to `https://straight-up-digital-site.netlify.app/api/private-enquiries` (or the verified production custom-domain equivalent).
4. Open the local CRM and use **Bulk actions → Sync website enquiries now**. The result shows only aggregate imported/acknowledged/retry counts; enquiry content stays private. The primary Zoho notification continues as before.

If Netlify production credits still prevent a deploy, wait for the credit reset or obtain explicit approval for a paid change before trying a production deploy. Do not use deploy previews to test this feature: the endpoint deliberately refuses non-production contexts, and production private data must not be exposed to previews.

## Privacy and billing boundaries

Quotes, invoices, payment records and recurring amounts are manual records only; the CRM does not process or accept money. The local backup is an ordinary private SQLite backup, not encrypted by this feature—protect the Windows account/device and backup folder accordingly. Erasure requires typing `DELETE`; if an owner-published report link exists, the local CRM first asks the production report service to revoke it. If that revocation cannot be confirmed, local erasure is stopped so the owner can retry safely.
