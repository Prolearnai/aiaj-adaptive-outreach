# AIAJ AWS Postal API transport (staged)

This folder contains a Node 22+ Postal HTTP API transport and nine tests.

## Scope

- The Postal HTTPS API at https://postal.aiajacademy.io has accepted requests from all three workspace credentials.
- The user confirmed all three cloud test messages are Held in Postal Development Mode.
- The existing **Vercel production** `aiaj-adaptive-outreach` deployment still uses a separate Nodemailer/SMTP campaign handler and is **not** represented by the older Python GitHub repository. This module is intentionally **not wired** to production traffic.
- This repository is public. Never commit API credentials or Vercel environment variable values here.

## Test

```sh
node --test postal-integration/postal-transport.test.mjs
```

## Production environment already configured

```text
POSTAL_BASE_URL=https://postal.aiajacademy.io
POSTAL_RADIO_FROM=outreach@aiajacademy.io
POSTAL_API_KEY_ACADEMY=<sensitive, stored in Vercel>
POSTAL_API_KEY_SOLUTIONS=<sensitive, stored in Vercel>
POSTAL_API_KEY_RADIO=<sensitive, stored in Vercel>
POSTAL_TRANSPORT_MODE=disabled
POSTAL_LIVE_ENABLED=false
```

The module's default `disabled` mode submits no messages. `test` allows only `test@example.invalid`. Live mode requires `POSTAL_LIVE_ENABLED=true`, `AIAJ_LIVE_ENABLED=true`, and an owner-authorized message. Note that these guards apply **only after** this module is integrated into the deployment.

## Remaining integration work

1. Obtain the **complete deployed** Vercel `api/campaign.js` source, including the Supabase claim/finish/failure lifecycle. Do not overwrite it with the older GitHub Python worker.
2. Preserve campaign ledger, recipient suppression/verification, idempotency, retries and error statuses.
3. Import the Postal transport **after** authorized, verified recipient claims. Route workspaces explicitly. Record `postal_api_accepted` and message ID; never equate API acceptance with delivery.
4. Deploy a preview with test mode and confirm `.invalid` messages are Held in all three organizations.
5. Keep production disabled while configuring DNS authentication, real bounce/return paths, SMTP egress, reputational monitoring and delivering a separate live authorization.

No code in this pull request changes current production sending.
