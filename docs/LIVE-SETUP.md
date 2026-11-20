# Parts Desk: live FaceTime and iMessage setup

Verified against Chert and hosting documentation on November 19, 2026.

The Vercel demo is public at https://parts-desk-zeta.vercel.app. Real calls run in the separate persistent FastAPI service. Its Docker image includes LiveKit RTC, OCR, local eSpeak speech (no Cartesia account), and the video instruction card. The same service receives signed iMessage photo webhooks and stores evidence on a persistent disk. Run **one replica and one Uvicorn worker**.

## Deployment status

Production Dockerfile, Render blueprint, health endpoint, and private webhook setup command are in this repository. **The media service has not been deployed.** The existing Render workspace reports “Workspace suspended … due to non-payment” and disables new services. All ten external credential fields in the local `.env` were empty at the time of checking. Nothing has called a real phone or received a real Chert photo yet.

Resolve the existing invoice yourself at https://dashboard.render.com/billing, or provide an available persistent host. This blueprint uses paid Render Starter compute plus a 1 GB disk; review the current price Render displays before creating it. Free sleeping services and Vercel functions are unsuitable for this call process. Starter memory capacity has not been tested with the Docker image; review memory metrics during the first OCR run.

After the workspace is available, deploy https://render.com/deploy?repo=https://github.com/kacytran1122/parts-desk using the root `render.yaml`. Keep `PARTS_DESK_LIVE=0` initially. Render generates `PARTS_DESK_OPS_TOKEN`; copy that value privately from **service → Environment** to use the live operator console. It is separate from the locally generated token. Wait for the service's `/healthz` to return `{"status":"ok"}`. Its actual `https://…onrender.com` address becomes the live console and webhook origin; no address is reserved yet.

## Exactly where to get the keys

| Environment field | Where to obtain it |
| --- | --- |
| `CHERT_TENANT` | https://console.trychert.com → select the messaging project → **API keys → Tenant slug**. Copy the slug, not a UUID. |
| `CHERT_API_KEY` | Same messaging **API keys** row → **Reveal signing secret**. This is the tenant signing secret used as a Bearer credential, despite this app's variable name. It is **not** the FaceTime `ftk_` key. If no longer available, ask the project admin; rotating it invalidates other users of that secret. |
| `CHERT_PROJECT_ID` | https://app.trychert.com → select the FaceTime project → **Developer** → its authenticated request example's `X-Chert-Project-Id` value. Use the project UUID. If your role does not expose it, ask the project admin. |
| `CHERT_PROJECT_KEY` | Same project → **Developer → Project API keys → create key**, with **`facetime:read` and `facetime:write`**. Save the `ftk_…` secret privately while visible. |
| `CHERT_FACETIME_LINE_ID` | Same project → **Lines** → assigned line details. Copy its line UUID, not its phone number. **Developer**'s list-lines request can also retrieve it if the UI omits it. A pending allocation/waitlist is not a usable line. |
| `LIVEKIT_URL` | https://cloud.livekit.io → select/create a project → **Settings → API keys** → select a key or **Create key** → project WebSocket URL, `wss://…livekit.cloud`. |
| `LIVEKIT_API_KEY` | Same LiveKit API key details → **API key**. |
| `LIVEKIT_API_SECRET` | Same LiveKit key details → **API secret**. Save privately before leaving. Eligible Chert projects may offer LiveKit credentials instead; use one matching URL/key/secret set. |
| `PARTS_DESK_TEST_PHONE` | Your own consenting test iPhone, in international format such as `+1…`. Complete Chert onboarding's verification from that phone; you must send its displayed verification code yourself. |
| `CHERT_WEBHOOK_SECRET` | Generated **after** the host address exists by `POST /api/v1/webhook-subscriptions`. Use the setup command below to save the one-time `subscription.secret` straight into the private `.env`; it is neither of the API keys above. |

For incoming photos, the messaging console's **Phone numbers** page must show an active photo-capable number in the same tenant. Send the certificate to that number from the test iPhone. The app currently receives photos and speaks the request during FaceTime; it does not send an outbound iMessage. There is no messaging-number environment variable because inbound events identify the tenant and sender.

**Two Chert products must be provisioned.** Access to a FaceTime line or shared-image UI does not by itself guarantee access to the console messaging tenant, attachment-download API, and tenant webhook subscriptions used here. If you can only see the shared-line interface, ask the sponsor/admin to provision these messaging capabilities or confirm the project supports them. Do not substitute the FaceTime key into the tenant messaging fields.

Copyable request for your Chert administrator (send it yourself):

> Please provision Parts Desk for outbound BYOR FaceTime video plus incoming iMessage photo webhooks. I need a project UUID, an assigned line UUID with outbound audio/video readiness and caller-camera capture enabled, and a project key with facetime:read/write. Please keep the line on customer_decides with no default managed Assistant. I also need a messaging-console tenant slug, its signing secret, an active photo-capable receiving number, and access to webhook subscriptions and attachment content downloads. Please verify my test iPhone as an allowed recipient. If this shared project only supports the separate shared-message API, please confirm that before issuing credentials.

## Enter credentials privately

On this Mac, the existing `/Users/kacy/emory/.env` is mode `0600`. Edit it locally; do not paste keys into a chat, commit them, or add them to Vercel's public browser environment. Do not overwrite its generated operator token. Keep live mode off while collecting keys.

```sh
cd /Users/kacy/emory
open -e .env
uv run --extra facetime --env-file .env python scripts/chert_setup.py check
```

The check prints **set/MISSING**, validates the intended live configuration, and never prints values, places calls, or sends messages. Missing webhook secret is expected until the next step.

Once the actual host address is known, replace `YOUR-HOST` below:

```sh
uv run --extra facetime --env-file .env python scripts/chert_setup.py register-webhook https://YOUR-HOST/api/chert/messages
```

This checks existing subscriptions, creates only a `message.received` subscription (version `2026-11-16`), and atomically saves its one-time secret to `.env` with mode `0600`. It does not retry the non-idempotent create request or overwrite an existing configured secret. If a network failure happens during creation, inspect the subscriptions before retrying; Chert may have created one even if the response was lost. An existing subscription's secret cannot be fetched again.

In Render **service → Environment**, enter all ten external fields above. Copy the webhook secret privately from the local file. Keep Render's generated `PARTS_DESK_OPS_TOKEN`, `/var/data`, and single-instance settings. Finally change `PARTS_DESK_LIVE` to `1` and save/redeploy. Startup rejects incomplete live settings. `/healthz` is liveness only; authenticated `/api/config` must show `mode: live`, `facetime_ready: true`, and `messaging_ready: true`. These flags mean configured, not provider-tested.

## Required line settings and first real test

1. In Chert **Lines → Integration** (operator views: **Workers & Lines → Integration**), use `customer_decides`, no default managed Assistant, and enable caller-camera capture with the required consent. Check separate outbound audio, outbound video, and caller-camera capabilities; an audio-ready line is insufficient.
2. Open the **persistent service's** root URL and unlock it using that host's operator token. The Vercel demo stores its own browser cases and does not automatically connect to this private service.
3. Begin an inspection and place one FaceTime call to the single verified test phone. Accept it; confirm the phone hears the greeting and sees the instruction card.
4. Point the camera at the printed synthetic tag, capture the QR, confirm the readback and identifiers, then arm the certificate-photo window. Send **one JPEG/PNG**, preferably under 4 MiB, to the active messaging number from that same iPhone. Avoid HEIC and albums.
5. Confirm that a signed webhook produces a pending candidate, inspect its evidence crop, and explicitly confirm it. Test a matching serial with the missing issuer section; it should still flag issuer verification.
6. End the call and confirm Chert reports a terminal state and frees the line. Then restart the host and verify the inspection/evidence persisted. Do not redeploy during a call: the disk persists, but active RTC sessions and frame buffers do not.

The implementation has mocked-provider and real image-processing tests. Docker startup, deployed media transport, provider authorization, and real iPhone behavior remain unverified until the hosting and credential blockers are resolved. No training result establishes aircraft-part installation eligibility.

## Official references

- https://docs.trychert.com/facetime/dashboard.md
- https://docs.trychert.com/facetime/authentication.md
- https://docs.trychert.com/facetime/outbound.md
- https://docs.trychert.com/api/quickstart.md
- https://docs.trychert.com/api/webhook-subscriptions.md
- https://docs.livekit.io/reference/telephony/connectors-api/
- https://render.com/docs/blueprint-spec
