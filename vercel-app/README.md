# Deployed Vercel Campaign Handler — AWS Postal Integration

This folder contains the 2026-11-20 source from the Vercel production campaign handler with a guarded AWS Postal API transport.

## Verified deployments
- Preview: `dpl_ACLhxb9yRzUyKz7p86P1izpwdX6f` (READY; HTTP 200 health)
- Production: `dpl_ECzK5pazpoREyXFp1XS22y5FHfZv` (READY; HTTP 200 health)
- Previous production rollback candidate: `dpl_Auegi5n6iSPKVqBb2xjoEhYPo2LP`
- Production URL: https://aiaj-adaptive-outreach.vercel.app/api/health
- Supabase: project `mhchzlxnsgvlhwqhoyub`, migration `add_postal_delivery_states_20261120` applied.

## Environment
The three `POSTAL_API_KEY_*` variables are Vercel **sensitive production secrets**, not present in source.
`POSTAL_BASE_URL=https://postal.aiajacademy.io` is configured in production.
`POSTAL_TRANSPORT_MODE=disabled` keeps the original SMTP route working. **It does not stop SMTP.**
`POSTAL_LIVE_ENABLED=false` keeps the Postal live path disabled.

## API behavior
The existing `/api/campaign` remains authenticated by `AIAJ_ADMIN_TOKEN`, uses Supabase `claim` and `state`, returns original SMTP outcomes in disabled mode, and adds workspaces `academy`, `solutions`, `radio` in Postal test/live mode.
Postal `test` mode only permits addresses ending in `@example.invalid`; requires `workspace` and `ownerAuthorized:true`; records `postal_api_accepted` with an API message ID, not a delivery confirmation. Never retry automatically after uncertain outcome.

## Remaining to enable live Postal
A verified-contact source-of-truth gate is mandatory. Current `outreach.campaign_contacts` entries were all `legacy_unreviewed` / `send_approved=false` at inspection, so live mode is intentionally blocked in code.
Complete DNS mail authentication, return-path, bounce event processing and SMTP egress verification before authorizing live Postal delivery. Production is currently still on SMTP.

Local integration artifact (not committed to this public repository) includes 21 isolation and campaign tests and a copy of the original deployment source.
