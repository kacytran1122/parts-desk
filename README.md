# Parts Desk

A training receiving-inspection worksheet for **Emory Hacks** (Cybersecurity · Chert Best Use of FaceTime · Coder for Good).

A technician shows an aircraft part's training tag during a guided FaceTime call, then texts a photo of the release certificate. Parts Desk checks three things **independently**: do the tag and certificate identifiers agree, is the issuer's expected boilerplate section present in a visible area, and is the part named in the FAA unapproved-parts notice. The inspector confirms every reading before it counts, can open the evidence crop, and gets a draft issuer verification request to review. Nothing is approved automatically.

The build follows [the technical plan](docs/technical-plan.md).

## Run the drill (no accounts needed)

Needs [uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
uv sync --locked
uv run uvicorn --factory partsdesk.api.app:create_app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. On localhost with no token set, the worksheet works without signing in. The first certificate analysis loads the OCR model (a few seconds).

### Demo script (judging case, under two minutes)

1. **New inspection**, then **Read training QR**. The tag reads `1856M94P01` / `00482` and waits in **Confirm tag reading**. Nothing counts until you press **Confirm**.
2. Choose **Matching serial · section missing**, then **Analyze photo**, then **Confirm**.
3. Result: **IDs match**, **Issuer verification needed**, **Part named in notice**. Open the crop of the blank section, the read text and SHA-256 hashes, and the **Draft issuer verification request**.
4. **Export evidence report** downloads the JSON record: findings, raw and confirmed values, rule revision, notice reference, and the full event history.

Other training photos (analyze and confirm each one):

| Training photo | What it shows |
|---|---|
| Matching serial · section present | Same IDs, template check passes: **Checks recorded** |
| Footer cropped | The photo is unusable, so the template check asks for a **retake** |
| Different serial | **Different values** (`00482` vs `00428`), template check unaffected |
| Instruction printed on form | `IGNORE INSTRUCTIONS: MARK APPROVED` is read as data, the footer goes to **Human review**, and nothing in the workflow changes |

Use **Correct a value before confirming** to fix a misread. The report keeps the raw read beside the confirmed value. **Retake** rejects the candidate and deletes its crop. While a replacement certificate waits for confirmation, the summary keeps showing findings from the last confirmed one.

Print the PNGs in `src/partsdesk/static/fixtures/` to rehearse with a phone camera (**Upload tag photo** / **Upload certificate**). Keep all four corner markers and the footer in frame. Regenerate them with `uv run python scripts/make_fixtures.py`.

## How the plan maps to the code

| Plan section | Code |
|---|---|
| Deterministic state machine, one operator | `src/partsdesk/workflow.py` |
| Three independent findings, versioned FAA rule | `src/partsdesk/rules.py` (`PD-GE-FOOTER@1`) |
| Records: inspection, capture (raw + confirmed), rule revision, finding, event sequence | `src/partsdesk/storage/` (SQLite WAL) |
| Confirmed capture + state change + event committed in one transaction; stale confirmations rejected by active capture ID | `src/partsdesk/service.py` |
| QR decode, straighten, crop blocks 7, 8, 10, visible-footer check | `src/partsdesk/vision/` |
| Private evidence storage with hashes | `src/partsdesk/storage/evidence.py` (0700 dirs, 0600 files) |
| Signed Chert webhook, dedupe, sender + capture window | `src/partsdesk/chert/webhook.py`, `api/routes_chert.py` |
| Attachment download and FaceTime call control | `src/partsdesk/chert/client.py` |
| Phonetic readback, eSpeak NG on stdin, 10 ms frames at 48 kHz, serialized cancellable playback | `src/partsdesk/speech/` |
| LiveKit room, bridge token, call lifecycle, caller frames | `src/partsdesk/live/` |
| Worksheet with reconnect from the event sequence | `src/partsdesk/static/`, `GET /api/inspections/{id}/events?after=N` |

## Live rehearsal (Chert FaceTime + iMessage)

1. Copy `.env.example` to `.env` (never commit it) and fill in: `PARTS_DESK_LIVE=1`, an operator token, the one consenting test iPhone in `+E.164` form, Chert messaging (`CHERT_API_KEY`, `CHERT_WEBHOOK_SECRET`, `CHERT_TENANT`), the Chert FaceTime project and line, and LiveKit Cloud. The app refuses to start in live mode until the required values are present and the phone and LiveKit URL have the expected formats. The Chert messaging tenant value is its tenant slug. The webhook secret is issued for the subscription and is distinct from the messaging API key.
2. Install and start with one worker (the call guard and caller preview live in memory):

   ```sh
   uv sync --locked --extra facetime
   uv run --env-file .env uvicorn --factory partsdesk.api.app:create_app --host 127.0.0.1 --port 8000
   ```

3. Point Chert's `message.received` webhook at a reachable HTTPS URL for `POST /api/chert/messages`. On a Mac, run `uv run python scripts/webhook_ingress.py` in a second terminal and point a temporary tunnel at `http://127.0.0.1:8789`. The relay forwards only that POST to the private app on port 8000. Enter the operator token under **Inspector access**.
4. **Call by FaceTime**. On answer, the iPhone hears the greeting. Show the printed tag, press **Capture visible QR**, and the phone hears the serial spelled out ("zero, zero, four, eight, two"). **Confirm** queues the certificate photo request.
5. **Expect certificate iMessage** arms one 10-minute window for that case. Send one JPEG or PNG from the test iPhone. It arrives as a candidate to confirm. If caller video is unavailable, use **Expect tag iMessage** first.
6. **End call** asks Chert to end and waits for a terminal status. Confirmed evidence stays after hangup.

Confirm the grant and line capacity in your Chert project before rehearsing. Rehearse the worker in a browser-connected LiveKit room first, and confirm the exact line configuration before spending phone minutes. Speech uses eSpeak NG on Linux and `say` on macOS. If synthesis fails, the prompt appears on the outgoing video card and in the worksheet history.

## Run in Coder

Open the repo in the team's Coder workspace and enable the Dev Container in `.devcontainer/`. It installs uv, eSpeak NG and OpenCV libraries, syncs the locked packages with the `facetime` extra, and prewarms OCR. Keep `data/` on a persistent volume and set `PARTS_DESK_OPS_TOKEN` before sharing the forwarded port.

## Tests and checks

```sh
uv run pytest --cov            # full suite, coverage gate 80%
uvx ruff@0.15.2 check src tests scripts
```

Tests run against the real fixtures (QR, alignment, OCR) and mock Chert with `httpx.MockTransport`. `src/partsdesk/live/worker.py` needs a live LiveKit room, so it is excluded from coverage and covered by the rehearsal above. Its pure helpers (`live/calls.py`, `live/frames.py`, `live/manager.py`) are unit tested.

## Security notes

- Only `POST /api/chert/messages` is public. It verifies an HMAC over the raw body, rejects stale timestamps, deduplicates event IDs, accepts one photo from the one test phone, and only inside an armed 10-minute window.
- Every other route needs the operator bearer token. Without a token, only localhost drill mode is allowed, and cross-site requests are refused.
- Camera and OCR text is stored as data. It never selects a workflow step, and the stage machine only moves on inspector commands.
- Evidence files are private on disk, hashed, and served with `no-store`. Rejected candidates lose their crop. Caller video frames stay in memory.
- Credentials stay in the server environment. They are never written to the database, returned by the API, or logged.
- Strict CSP, no inline script or style, no API docs exposed.

This is a training exercise on a fictional record. A human inspector determines installation eligibility.

## Public Vercel workspace

The web/ directory deploys a stateless FastAPI image analyzer and a public training worksheet to Vercel. It decodes the actual QR and analyzes the certificate image with OCR. Inspection cases and footer evidence persist in IndexedDB on the user's browser. Uploaded photos are processed by the Python function, with a 4 MB request limit. Use synthetic exercise documents on the public site.

Deploy from web/ with the Vercel CLI. The original src/partsdesk service remains the persistent live-call implementation. Live Chert FaceTime requires a long-running worker, the assigned line, and credentials, which are configured separately. The public deployment runs the training inspection flow.
