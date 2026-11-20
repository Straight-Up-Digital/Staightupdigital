# Straight Up Digital CRM upgrade

> **Scope update — 20 November 2026:** This plan describes local SQLite CRM enhancements that are implemented in the legacy app. The owner has since specified that the CRM and related services must be accessible online through Cloudflare, with no local hosting. That cloud migration is not complete. Keep the local-only guardrails below; do not treat this plan's completion notes as completion of the Cloudflare CRM, audit-report, or payment requirements.

## Scope and safeguards

Build the requested eight upgrades into the existing private Python/SQLite CRM and its existing branded UI. Preserve current local edits in `crm_web.py`, `business_auditor.py`, and `audit_queue.py`; never overwrite the user's work. Keep the CRM and client records private and bound to `127.0.0.1`. Do not expose the CRM through a tunnel or deploy the Python CRM/database to Netlify. Netlify is only the public website/enquiry edge; payments remain manual and tracked, not processed. All database changes must be additive/migratory, tenant-scoped, and tested using temporary databases/synthetic records. Do not print existing client data.

## Requested outcomes

1. **Lead communication timeline:** dated call, WhatsApp, email and in-person entries with message/outcome; reusable personalised templates using business name and latest audit finding; one-click WhatsApp draft.
2. **Quotes, invoices and payments:** quote lifecycle; branded invoice PDF; deposit/balance tracking; monthly payment status; hosting renewal dates and reminders. Track only; no payment collection or automatic billing.
3. **Website enquiries:** safely capture enquiries into CRM with source `website`, deduplicate repeat deliveries, and preserve the website's existing email notifications. The local app must initiate any sync; no public CRM endpoint or client-data tunnel.
4. **Audit progress and reporting:** compare previous/current scores and audit dates, re-audit workflow, and produce an owner-branded concise monthly report based on saved audits.
5. **Queue and batch operations:** inspect queued/running/failed audits and retry failures; bulk stage changes; town-wide audit queueing; CSV import with duplicate review and conservative merge.
6. **Won-client projects:** status and milestones (waiting for content / in design / live), domain/hosting notes, go-live date, and renewal/follow-up dates.
7. **Safety and compliance:** one-click SQLite-consistent backup; retain lead source/consent/opt-out lineage; owner-confirmed erasure of contact and associated personal report data while retaining only suppression hashes where needed; require authentication before any future non-local CRM access.
8. **Sales dashboard:** stage conversions, weighted/unweighted pipeline forecast, town/sector conversion indicators and response/follow-up timing.

## Implementation sequence

- **A. Foundation:** record a pre-migration SQLite backup; add tenant-scoped tables and migration-safe operations; regression tests; localhost-only guardrails.
- **B. Daily selling:** activity timeline, personalised message templates, WhatsApp draft, quote/invoice/payment/renewal ledger, client project tracking.
- **C. Operations and insights:** audit queue/retry/bulk actions, safe CSV duplicate-review/import, before/after audit comparison and monthly report, sales analytics.
- **D. Privacy and website intake:** backup/export and confirmed erasure tools; authenticated website-enquiry sync that preserves Zoho email delivery, with exact Netlify configuration/deploy steps if production credits remain unavailable.
- **E. Verification and handoff:** tests, local UI smoke checks, file/diff review, then GitHub sync only for code (never SQLite data, secrets, generated invoices or reports). Netlify deployment may remain blocked by the known production-credit limit; never publish the private CRM.

## Progress

- Baseline before changes: 55 tests passed, 3 subtests passed (15 Nov 2026).
- Existing functionality already found: lead pipeline and follow-ups; audit history and score history; durable audit-job rows; CSV export; source URLs, consent fields and do-not-contact suppression list; local-only listener.
- Working-tree edits preserved: `audit_queue.py`, `business_auditor.py`, and `crm_web.py` were modified before this task began.
- **Local CRM implementation:** Completed. Added the timeline/templates, manual quotes/invoices/payments/renewals, delivery projects, audit queue/retry and bounded town batches, CSV file/paste preview with explicit duplicate decisions, score movement and monthly summaries, conversion/follow-up dashboard, SQLite backup, contact opt-out/erasure, and website-intake sync client. Website imports preserve consent/source separately, default outreach to off, and are idempotent without resetting a manually changed pipeline stage.
- **Privacy boundaries:** Website enquiry sync is local-initiated over HTTPS; the public site has no direct access to the local CRM. The CRM refuses non-local listener addresses. Online report revocation is attempted before a typed-confirmation erasure; failure prevents local deletion so the owner can retry.
- **Pre-change backup:** Created before adding the new tables. The backup and customer records are not included in Git.
- **Local tests:** Full suite passes: 73 tests and 10 subtests. Python modules compile, and isolated synthetic page-render smoke tests pass. Live browser/production-flow checks remain pending the production deployment.
- **Website source:** Added private production-only Netlify Blob capture after successful existing email delivery, plus an authenticated production-only read/ack API and report-revocation operation. Website unit suite passes (38 tests), `npm run build` passes, and the changed functions were bundled locally. Source changes are pushed to GitHub.
- **Netlify secret configuration:** The connected Netlify MCP successfully added `CRM_ENQUIRY_SYNC_TOKEN` as a secret in the Production context, with Builds, Functions, and Runtime scopes; the Netlify UI verified there is no preview, branch, or local-development value. The matching token remains only in the ignored local `.env` and Netlify's secret store.
- **Remaining external setup:** Netlify reports production deploys are paused due to operational credits. The new website source is not live, so production sync and report-revocation behavior remain unverified. Once production deploys resume, deploy the pushed source, confirm `CRM_ENQUIRY_SYNC_URL` is present in the ignored local `.env`, and run synthetic end-to-end tests; see [`CRM_UPGRADE_SETUP.md`](CRM_UPGRADE_SETUP.md). No paid change or production deployment was made.


## Cloudflare side-by-side migration support (16 November 2026)

The CRM's website-enquiry sync and owner report publish/revoke clients now accept an exact, explicitly configured `workers.dev` hostname. Cloudflare has separate local `.env` settings for the Worker hostname, URLs and token values; the existing Netlify URLs/tokens remain unchanged as defaults. Unknown hosts, non-HTTPS URLs, ports, userinfo, query strings, arbitrary report paths, and non-exact report tokens are rejected. The Worker-specific values have not been populated because no Cloudflare deployment exists yet.

Added six synthetic tests covering exact-host opt-in, route allowlisting, separate Worker credentials, Netlify-default preservation, and report revocation URL restrictions. The full CRM suite passes: 73 tests and 10 subtests. No real client data or production credentials were used in these tests.
