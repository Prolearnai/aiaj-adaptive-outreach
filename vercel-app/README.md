# AIAJ AWS Postal transport — current implementation

This directory contains the production Vercel campaign handler and Postal HTTPS transport. The original Namecheap SMTP route remains available only when `POSTAL_TRANSPORT_MODE=disabled`.

## Live proof — November 20, 2026 (Pacific)

- Production Vercel `/api/health` returned HTTP 200 with `postalMode: "live"`.
- Vercel successfully connected over HTTPS to `https://postal.aiajacademy.io/api/v1/send/message`.
- Supabase `outreach-ledger` supports `verify` and invokes `public.aiaj_postal_authorized`.
- The verification check requires an explicitly approved recipient with `verification_status='source_verified'`, a nonempty source URL, a verification timestamp, and no suppression.
- The campaign handler performs verification **before** claiming a message in the ledger and before submitting it to Postal. Recipient-provided claims of verification are never trusted.
- Live Postal test campaign `postal-selftest-20261120-final` was recorded as `postal_api_accepted`. The matching test email arrived in the connected Gmail inbox.
- A replay returned `duplicate`; an unverified campaign returned `held_unverified`, without sending.
- The 212 legacy Academy drafts are held after reconciliation against existing Namecheap Sent messages; they must not be resent as new introductions.

## Production configuration

Vercel environment variables `POSTAL_BASE_URL`, `POSTAL_API_KEY_ACADEMY`, `POSTAL_API_KEY_SOLUTIONS`, `POSTAL_API_KEY_RADIO`, `POSTAL_TRANSPORT_MODE`, `POSTAL_LIVE_ENABLED`, and `AIAJ_LIVE_ENABLED` are configured separately from source. Never commit secrets. Live mode uses `POSTAL_TRANSPORT_MODE=live`, `POSTAL_LIVE_ENABLED=true`, and `AIAJ_LIVE_ENABLED=true`.

The engine requires its configured admin token. The connected MCP gateway supplies campaign items after owner authorization. A routing adapter currently restores the missing workspace and owner-authorization fields only for authenticated requests; the upstream Engine validates the admin token independently.

## Important distinctions

`postal_api_accepted` means the Postal API accepted a message. It is **not** proof of final delivery. Gmail receipt was separately verified for the single live test. Automated 5,000/day throughput and AWS SES account quota are separate operational checks.

The Supabase scheduled sending queue may be disabled independently of the Engine's authenticated campaign endpoint. Do not enable it without checking the existing outbound job ledger, suppressions, prior sends, and source verification.
