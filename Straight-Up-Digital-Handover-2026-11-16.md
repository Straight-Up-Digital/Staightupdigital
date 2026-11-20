# Straight Up Digital website — developer handover

> **Historical snapshot:** This handover describes the state observed on 16 November 2026. Since then, the website Worker has been deployed to `workers.dev`; the current Cloudflare and CRM status is recorded in the newer migration/setup notes. The Python CRM is still local and must not be presented as the completed Cloudflare CRM.

**Prepared:** 16 November 2026 (South Africa time)
**Scope:** website repository, Cloudflare migration, legacy Netlify path, and launch blockers. This handover is a local file outside the website repository. No source changes, merge, push, build, or deployment were made for this handover.

## 1. Repository and current Git state

- **Local repository:** `C:\Users\Saint\straight-up-digital-site`
- **Git remote shown by the local checkout:** <https://github.com/Straight-Up-Digital/straight-up-digital-site.git>
- **Latest implementation branch:** `cloudflare-workers-migration`
- **Latest branch commit:** `6c396c9` — `Finalize pre-domain launch checklist`
- **Branch synchronization:** At the time of the read-only `git status` and branch inspection, the working tree was clean and local `cloudflare-workers-migration` matched `origin/cloudflare-workers-migration` at `6c396c9`. Therefore the latest site-repository source work is on GitHub on that branch.
- **`main`:** Local `main` and its existing `origin/main` tracking ref were both `e81cb4a` (`Update CRM sync deployment status`). The Cloudflare migration commits have **not** been promoted to `main`; no merge or push to `main` was made. Remote tracking refs were not freshly fetched during this handover, so the branch comparison reflects the checkout's available refs at inspection time.
- **Repository-name discrepancy:** Earlier task text named `stjohntoperformer-sudo/straight-up-digital-site`; the current checkout's `origin` URL is under `Straight-Up-Digital/straight-up-digital-site`. Verify the intended canonical GitHub repository before a future push.
- **What is workspace-only:** The handover file itself (`C:\Users\Saint\business_auditor\Straight-Up-Digital-Handover-2026-11-16.md`) and a task note (`C:\Users\Saint\business_auditor\.work\straight-up-digital-netlify-deployment-notes.md`) are outside the site repo and not on GitHub. They are not site source. No uncommitted site-repository source files were reported by `git status` before this handover file was written. Tool transcripts and temporary result files are also local scratch artifacts, not repository changes.

## 2. Stack

The project uses **Astro 7.4.11** (lockfile-resolved; `package.json` range `^7.4.11`) and builds to **static output** (`output: "static"`, directory format). It does **not** use the `@astrojs/cloudflare` adapter; that package is not in `package.json`. A separate custom Cloudflare Worker entry point, `cloudflare/worker.ts`, serves the built `dist/` directory as static assets and handles API/report routes. `@astrojs/sitemap` is the Astro integration.

### Every direct package dependency in `package.json`

**Dependencies**

- `@astrojs/sitemap` — `^3.7.4`
- `@fontsource-variable/inter` — `^5.3.0`
- `@fontsource-variable/montserrat` — `^5.3.0`
- `@netlify/blobs` — `^11.1.9`
- `astro` — `^7.4.11` (lockfile resolves `7.4.11`)
- `nodemailer` — `^10.5.0`

**Development dependencies**

- `@astrojs/check` — `^0.9.12`
- `@cloudflare/workers-types` — `^5.20261116.1`
- `@netlify/functions` — `^6.1.1`
- `@types/nodemailer` — `^8.0.2`
- `netlify-cli` — `^27.17.0`
- `typescript` — `^5.9.3`
- `wrangler` — `^4.163.0`

The package engine requirement is Node `>=22`; `netlify.toml` pins Netlify builds to Node `24`. The local Node runtime observed during this read-only review was `v26.1.0`.

## 3. Build and deployment

Run from `C:\Users\Saint\straight-up-digital-site` after installing locked dependencies:

```sh
npm ci
npm run build:cloudflare
```

`npm run build:cloudflare` runs Astro's static build with `PUBLIC_FORM_MODE=email`, so the generated forms post to the Worker-handled email route rather than relying on Netlify Forms detection. Output is **`dist/`**.

The Wrangler deployment command configured for the production environment is:

```sh
npx wrangler deploy --env production
```

This deployment command was **not run**. It would publish the Worker and static assets; no Cloudflare production Worker deployment has occurred. Configure and verify the required production secrets and public-routing settings before running it. The project also has `npm run build` (plain `astro build`, using the normal/default form mode), `npm run check`, `npm test`, `npm run check:worker`, `npm run worker:dry-run`, and `npm run test:links`.

Recorded—not re-run for this handover—verification on the migration branch: 56 website tests; Astro check with zero errors/warnings/hints; Worker TypeScript check; an 11-page Astro build; test and production Wrangler dry-runs; 234 internal-reference checks; and `git diff --check`. Dry-runs do not establish that deployed Firestore or real SMTP calls work.

## 4. Cloudflare configuration and required names

There is a **`wrangler.jsonc`**, not a `wrangler.toml`. Its current complete contents are:

```jsonc
{
  "$schema": "./node_modules/wrangler/config-schema.json",
  "name": "straight-up-digital-migration",
  "main": "./cloudflare/worker.ts",
  "compatibility_date": "2026-11-16",
  "compatibility_flags": ["nodejs_compat"],
  "workers_dev": true,
  "assets": {
    "directory": "./dist",
    "binding": "ASSETS",
    "not_found_handling": "404-page",
    "run_worker_first": ["/api/*", "/.netlify/functions/*", "/report/*"]
  },
  "vars": {
    "APP_ENV": "development",
    "EMAIL_PROVIDER": "console",
    "ENQUIRY_TO_EMAIL": "straightupdigital@zohomail.com",
    "ENQUIRY_FROM_EMAIL": "straightupdigital@zohomail.com",
    "ZOHO_SMTP_HOST": "smtp.zoho.com",
    "ZOHO_SMTP_PORT": "465",
    "AUDIT_REPORTS_ENABLED": "false",
    "FIRESTORE_PROJECT_ID": "",
    "PUBLIC_REPORT_HOSTS": "straight-up-digital-site.netlify.app,straightupdigital.co.za,www.straightupdigital.co.za"
  },
  "triggers": { "crons": ["15 2 * * *"] },
  "env": {
    "test": {
      "name": "straight-up-digital-migration-test",
      "workers_dev": false,
      "vars": {
        "APP_ENV": "test",
        "EMAIL_PROVIDER": "console",
        "ENQUIRY_TO_EMAIL": "straightupdigital@zohomail.com",
        "ENQUIRY_FROM_EMAIL": "straightupdigital@zohomail.com",
        "ZOHO_SMTP_HOST": "smtp.zoho.com",
        "ZOHO_SMTP_PORT": "465",
        "AUDIT_REPORTS_ENABLED": "false",
        "FIRESTORE_PROJECT_ID": "straight-up-digital-test",
        "PUBLIC_REPORT_HOSTS": "straight-up-digital-site.netlify.app,straightupdigital.co.za,www.straightupdigital.co.za"
      },
      "triggers": { "crons": [] }
    },
    "production": {
      "name": "straight-up-digital-cloudflare",
      "vars": {
        "APP_ENV": "production",
        "EMAIL_PROVIDER": "zoho-smtp",
        "ENQUIRY_TO_EMAIL": "straightupdigital@zohomail.com",
        "ENQUIRY_FROM_EMAIL": "straightupdigital@zohomail.com",
        "ZOHO_SMTP_HOST": "smtp.zoho.com",
        "ZOHO_SMTP_PORT": "465",
        "AUDIT_REPORTS_ENABLED": "false",
        "FIRESTORE_PROJECT_ID": "straight-up-digital-prod",
        "PUBLIC_REPORT_HOSTS": "straightupdigital.co.za,www.straightupdigital.co.za"
      },
      "triggers": { "crons": ["15 2 * * *"] }
    }
  }
}
```

**Important routing check:** root configuration says `workers_dev: true`; the test environment explicitly changes it to `false`, but the production block does **not** explicitly set it to `false`. The migration notes say public access should remain disabled, so resolve the effective production `workers.dev` behavior and explicitly secure/disable it before deployment. No Worker is deployed now, so there is no live Worker hostname to report.

**Worker binding:** static assets binding `ASSETS`; there is no D1, KV, or R2 binding in this configuration. Firestore is accessed through its REST API from the Worker. Worker cron cleanup is scheduled at `15 2 * * *` in production. The Firestore databases already provisioned are `straight-up-digital-test` and `straight-up-digital-prod`, both documented as Spark/no-billing, Standard `(default)`, `africa-south1` (Johannesburg), empty, and with scheduled backups disabled. No service-account keys or Cloudflare Worker secrets have been created/configured per the migration documentation.

**Non-secret Worker variables and flags:** `APP_ENV`, `EMAIL_PROVIDER`, `ENQUIRY_TO_EMAIL`, `ENQUIRY_FROM_EMAIL`, `ZOHO_SMTP_HOST`, `ZOHO_SMTP_PORT`, `FIRESTORE_PROJECT_ID`, `PUBLIC_REPORT_HOSTS`, and `AUDIT_REPORTS_ENABLED`. The file sets `AUDIT_REPORTS_ENABLED=false` in production, so the Cloudflare report feature is off until deliberately enabled after checks.

**Secret names (values intentionally omitted):**

- Required for Firestore: `GOOGLE_FIRESTORE_SERVICE_ACCOUNT_JSON` (exact identifier used by code; not `FIRESTORE_SERVICE_ACCOUNT_JSON`) and the matching non-secret `FIRESTORE_PROJECT_ID`.
- Zoho SMTP: `ZOHO_SMTP_PASSWORD`; `ZOHO_SMTP_USERNAME` is supported and defaults in code to the configured sender if omitted.
- Private local-CRM intake: `CRM_ENQUIRY_SYNC_TOKEN`.
- Owner report publication/revocation: `AUDIT_REPORT_PUBLISH_TOKEN`.
- Rate-limit hashing: `RATE_LIMIT_HASH_SECRET`.
- Optional bot protection: runtime secret `TURNSTILE_SECRET_KEY` and build-time public key `PUBLIC_TURNSTILE_SITE_KEY`.
- Alternative email providers, not needed when using Zoho: `RESEND_API_KEY`, `POSTMARK_SERVER_TOKEN`, and optional `POSTMARK_MESSAGE_STREAM`.

No secret values are included in this document. Configure different Firestore credentials for test and production. The migration notes require owner approval before creating long-lived service-account keys. Johannesburg storage only describes the Firestore database location; it does not alone establish POPIA compliance or guarantee all Worker/email processing stays in South Africa.

## 5. Netlify remnants and which routes use them

The migration branch intentionally keeps Netlify as the legacy/rollback implementation. It has **not** been fully decoupled from Netlify.

### Forms and pages

- `public/__forms.html` statically declares two Netlify Forms: **`audit-request`** and **`contact-enquiry`**.
- `src/pages/digital-audit.astro` imports the shared `EnquiryForm.astro` with `variant="audit"`, naming the form `audit-request`.
- `src/pages/contact.astro` imports the same component with `variant="contact"`, naming the form `contact-enquiry`.
- `src/components/EnquiryForm.astro` defaults to Netlify mode unless build-time `PUBLIC_FORM_MODE=email` is set. Netlify's plain build script is `npm run build`; Cloudflare's `build:cloudflare` forces email mode. The current live deployment's exact `PUBLIC_FORM_MODE` build variable was not inspected in this handover. Repository docs record the Netlify Forms path and synthetic notification tests on 15 November 2026.
- In Netlify mode, submissions are Netlify Forms submissions and Netlify's Forms notification is configured in project settings for `straightupdigital@zohomail.com` per the repo's `TODO.md`/README. Synthetic contact and audit messages were previously verified in that mailbox. That is different from SMTP used by online-report responses.
- In email mode, the same component posts to `/.netlify/functions/submit-enquiry`. On the Cloudflare migration branch, `cloudflare/router.ts` intercepts that legacy-style URL and routes it to the Worker handler; it is a compatibility URL, not proof that a Netlify Function is required at Cloudflare runtime.

**Potential form-definition drift:** `public/__forms.html` contains duplicate legacy select options for Google Business Profile/content-social services and does not enumerate the newer hosting/domain options that the visible shared component adds. Reconcile/test the static Netlify form definitions if keeping Netlify Forms enabled.

### Netlify configuration and redirects

`netlify.toml` still configures Netlify build command/publish/functions directory, Node 24, cache headers, security headers/CSP, and these Netlify redirects:

- `/audit` → `/digital-audit/`
- `/audit-request` → `/digital-audit/#request`
- `/privacy-notice` → `/privacy/`
- `/terms-and-conditions` → `/terms/`
- `/home` → `/`

These are Netlify-specific. The inspected Worker router handles API/report paths then falls through to static assets; no equivalent Cloudflare redirect configuration was found in the tracked project tree. Do not assume `netlify.toml` redirects, caching, or security headers apply to a Worker deployment. Review/migrate the redirect and header behavior before cutover.

### Netlify Functions and Blobs

- `netlify/functions/submit-enquiry.mts` (`/.netlify/functions/submit-enquiry`): email-mode contact/audit submission, spam checks, email adapter, and post-delivery Netlify Blob CRM intake. The audit/contact forms on the two pages above use it only when built in email mode.
- `netlify/functions/client-audit-report.mts` (`/report/:token`): client-facing online report webpage and POSTed client next-step response. The client opens it through the private link after an owner manually publishes a report; it is not a static content page.
- `netlify/functions/publish-audit-report.mts` (`/.netlify/functions/publish-audit-report`): owner-token protected publication and revocation endpoint called by the private local CRM, not by a public page form. Uses Netlify Blobs in the legacy implementation.
- `netlify/functions/crm-enquiries.mts` (`/api/private-enquiries`): owner-token protected private CRM sync/acknowledgement endpoint for the local CRM, not a public visitor page.
- `netlify/functions/expire-audit-reports.mts`: scheduled Netlify Blob cleanup at `15 2 * * *`.
- Shared Netlify code is in `netlify/lib/`. `@netlify/functions`, `@netlify/blobs`, and `netlify-cli` remain in `package.json`.

The Cloudflare Worker has corresponding Firestore-backed route ports: `/api/enquiries` and `/.netlify/functions/submit-enquiry`; `/api/publish-audit-report` and `/.netlify/functions/publish-audit-report`; `/api/private-enquiries` and `/.netlify/functions/crm-enquiries`; and `/report/:token`. The static pages pass through `ASSETS`. Netlify code and Worker code coexist; the migration branch is not the same thing as a completed hosting cutover.

### Remaining Netlify deployment automation

- The Netlify dashboard showed the site remains linked to the GitHub repository with `main` as production branch. It also showed the existing published site remains live while deploys/Agent Runners are paused by the account's credit status.
- Before the stop request, the authorized Netlify dashboard change was saved and verified: **Build status = Stopped**. This disables automatic Netlify builds via its continuous-deployment setting; it does not unlink the site or delete the existing published deploy.
- **The build hook still exists** in Netlify (named `GitHub Actions production main (rotated 2026-11-14)`). Its URL is deliberately not copied here.
- `.github/workflows/netlify-production.yml` still triggers on pushes to `main` and has a manual `workflow_dispatch`; it POSTs the Netlify hook using the GitHub secret name `NETLIFY_BUILD_HOOK`. Whether that GitHub secret is currently present was not checked. The hook deletion was not completed. Thus the Netlify trigger path is not fully retired; a push to `main` may still invoke the GitHub workflow. Do not push until the owner explicitly resumes work and this is deliberately resolved.

## 6. Contact form: current behavior and destinations

The shared form accepts business name, website, email, optional phone, selected service, message, consent, source path, and anti-spam fields. Audit requests use `audit-request`; general enquiries use `contact-enquiry`. Browser-side validation and server-side validation/spam checks exist in email mode.

**Current/live path:** Netlify is still the last known live host. Source defaults to Netlify Forms unless `PUBLIC_FORM_MODE=email` is set. Project docs record that Netlify Forms notifications were changed to `straightupdigital@zohomail.com` and synthetic tests for both forms arrived on 15 November. The production build-time environment value was not inspected during this handover, so the exact mode in the current live artifact is not independently verified here.

**Intended Cloudflare path:** `npm run build:cloudflare` sets `PUBLIC_FORM_MODE=email`; the form posts to `/.netlify/functions/submit-enquiry`, which the Worker routes to its ported validation/email handler. On valid submission, it sends an email to the configured `ENQUIRY_TO_EMAIL` (production Wrangler value `straightupdigital@zohomail.com`) using Zoho SMTP when `EMAIL_PROVIDER=zoho-smtp`, and then writes the enquiry to Johannesburg Firestore for the private CRM sync. The Worker email/Firestore flow has not been tested live because no Worker deployment or production secrets exist. Do not send real client data through it until the test deployment and privacy checks are complete.

**Address discrepancy:** the website's `src/data/site.ts` currently publishes `Hello@straightupdigital.co.za` as the direct contact email. The form notification and Worker recipient are `straightupdigital@zohomail.com`. `PLACEHOLDERS.md` and other docs also discuss both addresses. Confirm the public contact mailbox independently; do not infer that the Hello mailbox is active from its display on the website.

## 7. Domain and hosting assumptions

- **Intended canonical domain:** `https://straightupdigital.co.za/`; the `www` hostname is also listed among expected report hosts. `astro.config.mjs` uses the apex domain for canonicals and the sitemap.
- **Last known live host:** `https://straight-up-digital-site.netlify.app/`; the handover's repo documentation reports Netlify production deploy `6af90efe` at commit `eaeb305`. Dashboard notice at this task said the published site remains live despite deploy-credit pause; a later build-status setting was saved as Stopped.
- **Domain status:** The custom domain was intentionally not purchased; DNS for apex and `www` previously returned NXDOMAIN (last noted in project docs on 14 November). No registrar DNS changes were made. Cloudflare custom-domain routing/HTTPS is not configured or live.
- The intended architecture is Cloudflare Workers serving static Astro assets with Worker APIs and Firestore in Johannesburg. No Cloudflare production deployment, DNS cutover, domain purchase, paid provisioning, or data migration has occurred. The private Python CRM remains a separate local application intended to stay bound to `127.0.0.1`; it is not part of this public website repository.

## 8. Project structure

The following is the tracked application structure at the inspected branch head. `.agents/skills/` contains a large committed skill library with reference documents; the grouped skill names are shown rather than repeating every reference-file path. `git ls-files` in the repository prints the exact complete tracked list.

```text
straight-up-digital-site/
├── .agents/skills/
│   ├── agents-sdk/                 # Agent SDK plus references
│   ├── basin/                      # basin API references
│   ├── cf-cli/
│   ├── cloudflare/                 # Worker platform references
│   ├── cloudflare-app-bootstrap/
│   ├── cloudflare-email-service/
│   ├── cloudflare-one/
│   ├── cloudflare-one-migrations/
│   ├── durable-objects/
│   ├── frameworks-on-cloudflare/
│   ├── gateway-workers/
│   ├── k2/
│   ├── netlify-access-control/
│   ├── netlify-agent-runner/
│   ├── netlify-ai-gateway/
│   ├── netlify-blobs/
│   ├── netlify-caching/
│   ├── netlify-config/
│   ├── netlify-database/
│   ├── netlify-deploy/
│   ├── netlify-edge-functions/
│   ├── netlify-forms/
│   ├── netlify-frameworks/
│   ├── netlify-functions/
│   ├── netlify-identity/
│   ├── netlify-image-cdn/
│   ├── netlify-mcp-servers/
│   ├── netlify-server/
│   ├── nextjs-on-cloudflare/
│   ├── sandbox-migrate-to-next/
│   ├── sandbox-next/
│   ├── sandbox-stable/
│   ├── turnstile-spin/
│   ├── web-perf/
│   ├── workers-best-practices/
│   ├── workers-development/
│   ├── workers-profiling/
│   └── wrangler/
├── .dev.vars.example
├── .env.example
├── .github/workflows/netlify-production.yml
├── PLACEHOLDERS.md
├── README.md
├── TODO.md
├── astro.config.mjs
├── cloudflare/
│   ├── email.ts
│   ├── firestore.ts
│   ├── http.ts
│   ├── private-routes.ts
│   ├── public-routes.ts
│   ├── router.ts
│   ├── scheduled.ts
│   ├── schema.sql                 # legacy/reference schema; Wrangler has no D1 binding
│   ├── security.ts
│   ├── storage.ts
│   ├── turnstile.ts
│   ├── types.ts
│   └── worker.ts
├── docs/
│   ├── cloudflare-migration-plan.md
│   ├── online-report-setup.md
│   ├── paystack-setup.md
│   ├── sa-hosting-and-domain-reseller-options.md
│   └── south-african-storage-setup.md
├── netlify.toml
├── netlify/
│   ├── functions/
│   │   ├── client-audit-report.mts
│   │   ├── crm-enquiries.mts
│   │   ├── expire-audit-reports.mts
│   │   ├── publish-audit-report.mts
│   │   └── submit-enquiry.mts
│   └── lib/
│       ├── client-report.mts
│       ├── crm-enquiry-store.mts
│       ├── crm.mts
│       ├── email.mts
│       └── validation.mts
├── package.json
├── package-lock.json
├── public/
│   ├── __forms.html
│   ├── brand/                     # official horizontal/stacked logos and symbol
│   ├── fonts/                     # local fonts and licence files
│   ├── app-icon-1024.png
│   ├── apple-touch-icon.png
│   ├── favicon-*.png / .ico / .svg
│   ├── icon-192.png
│   ├── icon-512.png
│   ├── og-image.png
│   ├── report-fonts.css
│   └── site.webmanifest
├── scripts/
│   ├── build-cloudflare.mjs
│   └── check-links.mjs
├── skills-lock.json
├── src/
│   ├── components/
│   │   ├── BackToTopButton.astro
│   │   ├── ClientReportPreview.astro
│   │   ├── Core.astro
│   │   ├── CtaBand.astro
│   │   ├── EnquiryForm.astro
│   │   ├── FaqList.astro
│   │   ├── ReportPreview.astro
│   │   ├── ScoreRing.astro
│   │   ├── SiteFooter.astro
│   │   ├── SiteHeader.astro
│   │   ├── StickyAuditBar.astro
│   │   └── WhatsAppButton.astro
│   ├── data/
│   │   ├── categories.ts
│   │   ├── faqs.ts
│   │   ├── navigation.ts
│   │   ├── pricing.ts
│   │   ├── services.ts
│   │   ├── site.ts
│   │   └── steps.ts
│   ├── layouts/BaseLayout.astro
│   ├── pages/
│   │   ├── 404.astro
│   │   ├── about.astro
│   │   ├── contact.astro
│   │   ├── digital-audit.astro
│   │   ├── how-it-works.astro
│   │   ├── index.astro
│   │   ├── pricing.astro
│   │   ├── privacy.astro
│   │   ├── robots.txt.ts
│   │   ├── services.astro
│   │   ├── terms.astro
│   │   └── thank-you.astro
│   └── styles/global.css
├── tests/
│   ├── client-report.test.mjs
│   ├── cloudflare-worker.test.mjs
│   ├── enquiry.test.mjs
│   ├── firestore-storage.test.mjs
│   ├── function.test.mjs
│   └── site-content.test.mjs
├── tsconfig.cloudflare.json
├── tsconfig.json
└── wrangler.jsonc
```

## 9. Placeholders, fictional material, and missing assets

- The branded logos, favicon/app icons, Open Graph image, and local font files are present in `public/`; no broken/missing logo asset was identified in this read-only inventory.
- The home page intentionally shows an **example** score/coverage and labels it as example data, not a real client. `/digital-audit/` includes a clearly labelled **fictional sample customer report**. Do not mistake either for a live audit or customer testimonial.
- `src/data/site.ts` has no founder photo, social-profile URLs, business hours, or formally defined service-area list. The site avoids displaying those missing details. No street address is supplied/published; the owner-approved locality is Port Alfred, Eastern Cape, South Africa.
- The site publishes `Hello@straightupdigital.co.za` as its contact address; the Zoho inbox is the form/Worker notification destination. Confirm whether the displayed mailbox is active.
- Hosting/domain prices and reseller fulfilment are requests/indicative offers, not proof of activated upstream reseller accounts. The launch checklist says reseller supplier operations remain outstanding. Paystack checkout/onboarding and recurring billing are not configured; customers should not be charged or promised automated provisioning.
- The profile claims only the confirmed locality; no broader service-area or social links are published. Service data marks some services as on-request/planned; keep their status accurate.

## 10. Known breakage, exact reported error, and prior attempts

### Deployment error supplied in the earlier Netlify handoff

No Cloudflare deployment was attempted in this session, so there is no Cloudflare deploy error to quote. The earlier user-supplied Netlify log reported this repository-preparation failure. The excerpt was visibly cut off after `exis` on its line 8:

```text
Line 8: Failed during stage 'preparing repo': Unable to access repository. The repository may have been deleted, the branch may not exis
Line 9: User git error while checking for ref refs/heads/main
```

The earlier message also quoted Netlify's diagnostic text as:

```text
Unable to access repository. The repository may have been deleted, the branch may not exist
```

The exact cause was not established. Prior suggestions were to check whether `main` exists, whether the repo is private/deleted, or whether Netlify's GitHub authorization/repository link needs reconnecting. Current local Git state shows both `main` and the migration branch refs, and the Netlify dashboard still shows a repository link, but neither proves Netlify's GitHub authorization is currently healthy. No fresh remote fetch or new Netlify build was performed here.

### Other known production gap

Project checklists report the last live Netlify deploy as commit `eaeb305` (`6af90efe` deploy), while GitHub `main` is later at `e81cb4a`. The live deployed Netlify report publisher is documented as POST-only; a synthetic DELETE/revoke request returned HTTP **405**. Newer source has revocation support, but it was not deployed to Netlify. Do not claim live Netlify report revocation works until verified on the live site.

### What was changed/attempted in this interrupted task

- The Netlify dashboard's **Build status** was set to **Stopped** and a later dashboard read confirmed it was saved. The published site was shown as still live.
- The existing Netlify build hook was observed but **not deleted**; attempts to target its delete control did not succeed. The GitHub workflow file remains. Do not promote/push to `main` while the owner has asked to pause; a future owner-approved step must retire or intentionally preserve these triggers.
- No merge to `main`, push, build, Cloudflare deployment, Netlify deployment, DNS/domain change, or paid provisioning was performed in this task. No Cloudflare credentials were created. Following the user's stop instruction, work is paused after producing this handover.

### Immediate known blockers before any Cloudflare production cutover

1. Resolve production `workers_dev` exposure ambiguity in `wrangler.jsonc` and ensure only the intended hostname/route is public.
2. Approve/configure separate Firestore service-account credentials and remaining secrets; run synthetic tests only on an unrouted test Worker first.
3. Test real Cloudflare-to-Zoho email delivery, Firestore persistence, CRM sync/ack, report publish/read/respond/revoke/expiry, and failure paths.
4. Migrate and test Netlify redirects, security headers/CSP, and caching for Workers; `netlify.toml` is not a Cloudflare configuration file.
5. Complete provider/data-transfer/retention/privacy review. Johannesburg Firestore alone does not settle POPIA or cross-border processing; Zoho mail and Cloudflare request processing remain in the flow.
6. Confirm the intended GitHub repository owner/URL. Promote only after the owner resumes work and specifically approves it.
7. Keep the custom domain unpurchased/unrouted until the tests and privacy gates pass; then separately authorize purchase and DNS cutover while preserving Zoho mail DNS records.
