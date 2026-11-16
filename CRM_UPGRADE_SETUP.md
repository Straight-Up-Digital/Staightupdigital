# Straight Up Digital CRM upgrade: final setup

## What is already implemented

The local CRM now includes the requested activity timeline and draft messages; quote, invoice, payment and renewal tracking; project status; audit queue/retry and bounded town batches; duplicate-reviewed CSV import; score history and monthly reporting; local backups; source/consent/opt-out tracking; guarded erasure; and owner-clicked website-enquiry sync. The Python CRM remains bound to `127.0.0.1`; do not expose it through a tunnel.

The public website keeps its existing Zoho email-delivery path. After a successful email send, the production function stores the enquiry in a private Netlify Blob queue. A separate authenticated production-only function returns those records only to the owner’s local CRM. The local CRM acknowledges (deletes) each remote item only after the SQLite import commits. The remote copy is not publicly readable, and the local sync does not grant marketing permission.

## Netlify configuration and deployment status

On 15 November 2026, the Netlify connector added `CRM_ENQUIRY_SYNC_TOKEN` to the `straight-up-digital-site` project. Netlify's environment-variable page verifies it is marked as a secret, scoped to **Builds, Functions, and Runtime**, and set for **Production only** (deploy previews, branch deploys, preview runners, and local-development contexts are empty). The same random value is in the ignored local `C:\Users\Saint\business_auditor\.env`; neither copy is in Git or this file. The Netlify CLI is still not installed, but the authenticated Netlify connector was used to configure the project.

The website source containing the private Netlify Blob queue and token-authenticated read/ack function has been pushed to GitHub but is not yet deployed to production. Netlify currently reports production deploys and Agent Runners are paused due to operational credits. No paid upgrade was made.

After production deploys resume:

1. Deploy the already-pushed website source to the **Production** context. Do not deploy this feature to a preview or branch context: the endpoint intentionally refuses non-production contexts.
2. Set `CRM_ENQUIRY_SYNC_URL` in the ignored local `.env` to `https://straight-up-digital-site.netlify.app/api/private-enquiries` (or the verified production custom-domain equivalent), if it is not already present.
3. Open the local CRM and use **Bulk actions → Sync website enquiries now** with a synthetic enquiry. Verify one local import and remote acknowledgement, then confirm Zoho delivery is unchanged; the result shows only aggregate counts and enquiry content stays private.
4. Test owner-authenticated report-link revocation against a synthetic report after deployment.

Wait for the credit reset or get explicit approval before any paid change. Do not expose the local CRM through a tunnel or send the secret via chat, email, or Git.

## Privacy and billing boundaries

Quotes, invoices, payment records and recurring amounts are manual records only; the CRM does not process or accept money. The local backup is an ordinary private SQLite backup, not encrypted by this feature—protect the Windows account/device and backup folder accordingly. Erasure requires typing `DELETE`; if an owner-published report link exists, the local CRM first asks the production report service to revoke it. If that revocation cannot be confirmed, local erasure is stopped so the owner can retry safely.


## Side-by-side Cloudflare Worker migration (not yet deployed)

The website's `cloudflare-workers-migration` branch has a separate Worker implementation and passed local tests/build checks, but it has not been deployed to Cloudflare; Netlify remains the live platform and the CRM's existing Netlify secrets/URLs remain untouched. The local CRM now supports an optional exact account-specific `workers.dev` host with separate Cloudflare-only tokens and URLs. It rejects wildcard hosts and does not opt into Cloudflare unless `CLOUDFLARE_WORKER_HOST` is explicitly set.

After a Cloudflare Worker has been deployed and its synthetic flows have passed, add these values only to the ignored local `.env` and the corresponding Cloudflare **Production** secret settings:

- `CLOUDFLARE_WORKER_HOST=<worker-name>.<account-subdomain>.workers.dev` (hostname only; no scheme or path).
- `CLOUDFLARE_CRM_ENQUIRY_SYNC_URL=https://<that-host>/api/private-enquiries` and `CLOUDFLARE_CRM_ENQUIRY_SYNC_TOKEN` matching the Worker secret `CRM_ENQUIRY_SYNC_TOKEN`.
- `CLOUDFLARE_AUDIT_REPORT_PUBLISH_URL=https://<that-host>/api/publish-audit-report` and `CLOUDFLARE_AUDIT_REPORT_PUBLISH_TOKEN` matching the Worker secret `AUDIT_REPORT_PUBLISH_TOKEN`.

Keep `CRM_ENQUIRY_SYNC_TOKEN`, `CRM_ENQUIRY_SYNC_URL`, `AUDIT_REPORT_PUBLISH_TOKEN`, and `AUDIT_REPORT_PUBLISH_URL` for the current Netlify production path until the owner separately approves a cutover. Use synthetic enquiries and reports for the Cloudflare test. Do not copy Netlify data, change DNS, or disable Netlify as part of this branch work.
