# Parts Desk technical plan

**Emory Hacks · Cybersecurity · Chert Best Use of FaceTime · Coder for Good**

Capture an aircraft part’s training tag during a guided FaceTime call. The technician texts a sharp photo of the release certificate to Chert. The worksheet checks the identifier match and the issuer-specific evidence independently. The judging case has matching serials and a visible but missing boilerplate section. An inspector can open the crop, see why the report asks for issuer verification, and review a draft verification request.

The runnable starter implements the training workflow below with QR decoding, bounded OCR, visible-footer checks, a private evidence view, and optional live Chert adapters. It uses RapidOCR on the supplied exercise template. [Run Parts Desk](../README.md)

## 1. Stack and service boundaries

| Layer | Choice for the hackathon | Job |
|---|---|---|
| Native video call | Chert FaceTime Calls API in Bring Your Own Room mode, with LiveKit Cloud | Place an outbound FaceTime call, bridge the caller's media into a room, and return agent audio and a generated video track to the iPhone. |
| Agent worker | Python 3.12 and LiveKit RTC SDK, asyncio | Subscribe to the bridge tracks, manage the inspection sequence, and publish spoken instructions and the comparison view. Run CPU work outside the media event loop. |
| Capture and parsing | OpenCV QR decode on the training tag, signed Chert attachment webhook, Pillow/OpenCV and CPU OCR for form crops | Obtain the tag identifier and preserve the original certificate photo. Evaluate field and footer visibility before scoring a finding. |
| Voice | Local eSpeak NG on Coder Linux, macOS `say` for local development, with worksheet controls | Use local speech for framing guidance and serial digits on the iPhone. |
| API and policy | FastAPI, Pydantic, httpx, Python rules | Start and end calls, validate capture and confirmation events, evaluate checks, and return one report from the confirmed evidence. |
| Storage | SQLite with WAL mode, plus a private directory for confirmed crops | Store one demo operator's inspections, events, and versioned FAA rule. Run the API and media worker on the same persistent host. |
| Browser worksheet | Existing semantic HTML/CSS and JavaScript, served by FastAPI, with polling in live mode | Keep the current UI and update it as the inspection progresses. Serve the final review record and source links. |
| Development and QA | Coder Linux workspaces, uv lockfile, Git and fixture-driven checks | Give teammates the same runtime, capture fixtures, and test commands. Rehearse the live integration on the designated demo workspace and iPhone. |

Use a deterministic state machine and one operator for the demo. Keep credentials server-side. Configure a private Coder workspace, prewarm the OCR model, and test the same worker in a browser-connected LiveKit room before spending another FaceTime minute. The current Free grant is advertised as 10 FaceTime minutes and 10 messages total within 24 hours. Confirm the event line’s exact configuration and limits first. [Chert pricing](https://www.trychert.com/#pricing) · [Coder workspaces](https://coder.com/explore/how-coder-works/workspaces)

## 2. Chert integration comes first

1. Create a new inspection with an allowlisted demo recipient. Allocate a unique LiveKit room and start the named worker. Publish one eligible, unmuted audio track that stays silent until the call is ready, and publish a holding card as the outgoing video track.
2. Mint a short-lived bridge token scoped to that room with a distinct participant identity. The backend creates the Chert outbound video call with the room URL, bridge token, agent identity, and selected line. Persist the returned call ID and reuse the same idempotency key and request on retry.
3. Start the greeting on `bridge_ready` or the corresponding configured `call.started` webhook. Use polling for the demo if it keeps setup simpler. Subscribe to the bridge's caller audio and video tracks. Track the inspection stage independently of call lifecycle events so a disconnect preserves confirmed evidence.
4. Let the technician show the training tag. Publish framing prompts and the readback. Ask for a sharp photo of the certificate in the iMessage thread. Arm one active inspection and match the verified handle and capture window. Keep essential content inside the center of the frame and check the readable size on the actual iPhone.
5. Handle no answer, a busy line, camera mute, stalled frames, hangup, and cancellation. Clear the current frame when the track is unavailable. Treat end-call acknowledgment as a request and observe terminal status before closing out the room. Keep confirmed captures available for review after hangup.

**First acceptance check.** The iPhone rings. A technician answers and hears the worker read “zero zero seven two” through FaceTime with the worker host's speakers muted. They move a printed QR tag and the worker sees changing frames. The inspector captures the QR and the iPhone hears the exact serial and photo request. Hang up and repeat the full call. The incoming stream can include FaceTime controls, a name, or self-preview, so use samples captured through the actual bridge to tune the crop. [Chert outbound API](https://docs.trychert.com/facetime/outbound) · [Chert audio and video](https://docs.trychert.com/facetime/audio-video)

## 3. Capture, speech, and evidence

Use a high-contrast physical tag with a QR at least a third of the tag width. The attached review measured 155 correct decodes across 156 synthetic compressed frames. Tune against actual Chert frames. If camera capture is unavailable, request a tag photo as well. Compare part and serial values as strings with leading zeros preserved.

- **Photo ingest.** Verify the Chert webhook over the raw bytes, deduplicate by event ID, match the enrolled sender and case, and retrieve authenticated attachment bytes with type and size limits. Save the submitted photo and its hash in private evidence storage. [Chert receiving replies](https://docs.trychert.com/api/receiving-replies)
- **Visibility.** Straighten the submitted certificate against the training template. Verify the footer and surrounding template anchors are in frame before evaluating the boilerplate. Crop blocks 7, 8 and 10 by field position. If text OCR is needed on call frames, begin with the review’s 25-pixel gate and spaced samples. Correlated reads can repeat an error. The review reports disabling MKL-DNN fixed a CPU crash for its PaddleOCR versions. Rehearse on the actual Coder host.
- **Evidence.** Report match, different values or insufficient evidence independently from the issuer-template rule. An unreadable or clipped footer requests a retake. A visible footer with missing expected text on the selected issuer template requests human review. Supplier and buyer remain unverified in the starter. The pilot should add independent purchase-order evidence.
- **Accessible readback.** Speak framing cues and use phonetic letters. Ask the user to confirm the item and capture, and draw accuracy from independent observations. Provide the same result in the worksheet and preserve readable evidence crops and source links. Use worksheet controls for the weekend.

### Local speech through FaceTime

1. Install eSpeak NG in the Coder workspace image. Use `espeak-ng -v en-us -s 145 --stdin -w /path/to/prompt.wav` from an asynchronous subprocess with a private temporary path, a short timeout, and the prompt passed as UTF-8 on stdin. Generate prompts from confirmed workflow state and the current capture candidate. Format identifiers before synthesis so `0072` reads “zero, zero, seven, two.” Generate static instructions ahead of time and render new serials on demand. [eSpeak NG command-line reference](https://github.com/espeak-ng/espeak-ng/blob/master/src/espeak-ng.c)
2. Decode the WAV into mono signed 16-bit PCM. If its sample rate differs from 48 kHz, resample with LiveKit's `rtc.AudioResampler` and flush it at the end of the utterance. Split the stream into 10 ms frames of 480 samples, padding the last frame with silence if needed. [LiveKit audio resampler](https://docs.livekit.io/reference/python/livekit/rtc/audio_resampler.html)
3. Feed each frame to `await source.capture_frame(frame)` on the one published LiveKit `rtc.AudioSource(48000, 1)` after the Chert bridge is ready. Serialize playback so prompts never overlap. On retake or hangup, cancel stale prompts and clear queued audio. If synthesis fails, show the prompt on the iPhone's outgoing video card and the worksheet so the operator can continue. [LiveKit audio publishing](https://docs.livekit.io/transport/media/publish/) · [LiveKit audio source](https://docs.livekit.io/reference/python/livekit/rtc/audio_source.html)

To try the same WAV-to-LiveKit path on a Mac, generate a sample with the built-in voice. This command was verified locally to produce a non-silent mono, 48 kHz, 16-bit PCM WAV.

```sh
say -v Samantha --file-format=WAVE --data-format=LEI16@48000 \
  -o readback.wav "Serial. Zero zero seven two. Say confirm or retake."
```

The complete key-free speech setup uses local synthesis and worksheet controls. Chert and LiveKit still need their own credentials for the live FaceTime demo.

## 4. Rules, data, and security

Keep three separate findings for identifier agreement, template anomaly, and FAA notice scope. The cited notice names GE bushing `1856M94P01` sold by AOG Technics to TAP Maintenance & Engineering. It describes falsified paperwork characteristics including missing User/Installer Responsibilities language. Use that characteristic for the visible-region training check and keep the notice’s scope visible. Show “issuer verification needed” with an evidence crop. Draft a request for the inspector to review and send. [FAA AOG Technics notice](https://www.faa.gov/aircraft/safety/programs/sups/upn/2023-AAE-EHL-20230801-713)

Suggested records:

| Record | Minimum fields |
|---|---|
| Inspection | ID, training case, current state, operator, timestamps, optional Chert call ID and LiveKit room ID |
| Capture | ID, inspection ID, source type, capture time, crop reference, raw and confirmed fields, confirmation time |
| Rule revision | ID, FAA notice ID, source URL and scope, reviewed predicates, version |
| Finding | Check, result, capture IDs, evaluated values, rule revision when applicable |
| Event | Monotonic inspection sequence, timestamp, event type, state change |

The starter exposes case creation, photo upload, a FaceTime call, QR capture, a signed Chert webhook, and authenticated evidence endpoints. A pilot can add explicit per-capture confirmation and a dedicated event stream. The worker records capture and state changes through shared application code on the demo host. Reconnect the worksheet by loading the latest snapshot and continuing from its event sequence. Commit the confirmed capture and its resulting state transition in one transaction. Use the active capture ID to reject late confirmations from a retake or prior session.

Treat camera text as data even if it contains instructions. Keep the authorized recipient, file paths, and FAA links outside the OCR output. Store credentials only server-side, process frames in memory, and retain only approved evidence crops. Use mock records in the demo. Export the report with findings, raw and confirmed values where they differ, notice references, and review status. A human inspector determines installation eligibility. Hash the submitted image and retained crop for traceability. The Security demo can show an instruction printed on a prop entering the report as data under the same restricted state machine.

## 5. Build order and acceptance

| Time from build start | Build | Acceptance check |
|---|---|---|
| First rehearsal | Verify the assigned test iPhone, outbound video, caller camera, signed attachment webhook and local speech. | One real call sees the tag and one actual iMessage photo appears on the same case. |
| Core build | Wire QR, a bounded certificate crop and the visible-footer rule. | Two forms share matching identifiers. Only the one missing the section flags for issuer review. A clipped footer asks for a retake. |
| Demo prep | Add spoken framing, source links, a case replay and the evidence view. | The judge can trace the reason to the image and verify that document text cannot change the workflow. |

Keep the demo call under two minutes. Record a successful live run and keep the browser-connected worker or labeled fixture replay ready. The attachment’s OCR numbers are from simulated compression. Measure legibility and response time on the actual Chert feed and Coder workspace before claiming a read rate.
