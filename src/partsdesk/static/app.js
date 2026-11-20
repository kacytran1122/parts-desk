"use strict";

const el = (id) => document.getElementById(id);
const EMPTY = "Not captured";
const POLL_MS = 1500;
const LABELS = {
  awaiting_evidence: "Awaiting evidence", match: "IDs match", different_values: "Different values",
  insufficient_evidence: "Insufficient evidence", section_present: "Section present",
  issuer_verification_needed: "Issuer verification needed", human_review: "Human review",
  retake_requested: "Retake requested", part_named_in_notice: "Part named in notice",
  part_not_named_in_notice: "Part not named in notice", retake_or_verify: "Retake or verify",
  inspector_review_needed: "Inspector review needed", checks_recorded: "Checks recorded",
  awaiting_tag: "Awaiting tag", confirming_tag: "Confirm the tag reading",
  awaiting_certificate: "Awaiting certificate", confirming_certificate: "Confirm the certificate reading",
  review_ready: "Ready for inspector review", closed: "Closed",
  training_fixture: "Training fixture", inspector_upload: "Inspector upload",
  facetime_frame: "FaceTime frame", chert_imessage: "iMessage attachment",
};
const WARN = new Set(["different_values", "insufficient_evidence", "issuer_verification_needed", "human_review",
  "retake_requested", "inspector_review_needed", "retake_or_verify"]);

let current = null;
let busy = false;
let cropKey = null;
let cropUrl = null;
let cameraUrl = null;

const label = (value) => LABELS[value] ?? value ?? EMPTY;
const put = (id, text) => { el(id).textContent = text ?? EMPTY; };
const route = (tail = "") => `/api/inspections/${current.id}${tail}`;

function auth() {
  const token = el("token").value.trim();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request(path, body) {
  const isForm = body instanceof FormData;
  const response = await fetch(path, {
    cache: "no-store",
    method: body === undefined ? "GET" : "POST",
    headers: { ...auth(), ...(body !== undefined && !isForm ? { "Content-Type": "application/json" } : {}) },
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof result.detail === "string" ? result.detail : "That request was not accepted.";
    throw new Error(detail);
  }
  return result;
}

function item(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function renderFindings() {
  for (const finding of current.findings) {
    const node = el(`f-${finding.check}`);
    node.textContent = label(finding.result);
    node.toggleAttribute("data-warning", WARN.has(finding.result));
  }
  put("review", label(current.review_status));
  el("review").toggleAttribute("data-warning", WARN.has(current.review_status));
  put("stage", label(current.stage));
}

function renderCaptures() {
  const { tag, certificate: cert, pending } = current;
  put("tag-part", tag?.confirmed?.part);
  put("tag-serial", tag?.confirmed?.serial);
  put("tag-source", tag ? `${label(tag.source)} · confirmed` : "");
  put("cert-part", cert?.confirmed?.part);
  put("cert-serial", cert?.confirmed?.serial);
  put("cert-source", cert ? `${label(cert.source)} · confirmed` : "");
  put("cert-description", cert?.confirmed?.description ? `Block 7: ${cert.confirmed.description}` : "");
  el("candidate").hidden = !pending;
  if (pending) {
    put("candidate-title", pending.kind === "tag" ? "Confirm tag reading" : "Confirm certificate reading");
    put("candidate-source", label(pending.source));
    put("candidate-reason", pending.reason);
    put("candidate-part", pending.raw.part ?? "Unread");
    put("candidate-serial", pending.raw.serial ?? "Unread");
  }
}

function renderEvidence() {
  const attempt = current.latest_certificate_attempt;
  put("evidence-reason", attempt?.reason ?? "Load a certificate photo to inspect its expected footer.");
  el("raw").hidden = !attempt;
  el("lines").replaceChildren(...(attempt?.observations ?? []).map((line) => item("li", line)));
  put("digest", attempt ? `Original SHA-256 ${attempt.original_sha256}${attempt.crop_sha256 ? ` · crop ${attempt.crop_sha256}` : ""}` : "");
  el("draft-box").hidden = !current.issuer_draft;
  put("draft", current.issuer_draft ?? "");
  put("rule", `${current.rule.id}@${current.rule.version}`);
  put("scope", current.rule.scope);
  el("faa").href = current.rule.source_url;
  replaceCrop(attempt);
}

function renderEvents() {
  put("seq", `Event ${current.last_seq}`);
  el("events").replaceChildren(...current.events.slice().reverse().map((event) => {
    const stage = event.to_stage ? ` → ${label(event.to_stage)}` : "";
    return item("li", `#${event.seq} ${event.detail}${stage}`);
  }));
}

function render() {
  el("desk").hidden = false;
  put("case", `CASE ${current.id.toUpperCase()}`);
  const live = current.mode === "live";
  put("mode-chip", live ? "LIVE REHEARSAL" : "TRAINING DRILL");
  el("live-tools").hidden = !live;
  el("expect-cert").hidden = !live;
  put("call-status", live ? `Call: ${current.call.status}` : "");
  renderFindings();
  renderCaptures();
  renderEvidence();
  renderEvents();
}

async function replaceCrop(attempt) {
  const key = attempt?.has_crop ? `${current.id}/${attempt.id}` : null;
  if (key === cropKey) return;
  cropKey = key;
  el("crop-wrap").hidden = true;
  if (cropUrl) URL.revokeObjectURL(cropUrl);
  cropUrl = null;
  if (!key) return;
  try {
    const response = await fetch(route(`/captures/${attempt.id}/crop`), { headers: auth(), cache: "no-store" });
    if (!response.ok || cropKey !== key) return;
    cropUrl = URL.createObjectURL(await response.blob());
    el("crop").src = cropUrl;
    el("crop-wrap").hidden = false;
  } catch {
    cropKey = null;
  }
}

async function perform(handler) {
  if (busy) return;
  busy = true;
  put("status", "");
  try {
    await handler();
    if (current) render();
  } catch (error) {
    put("status", error.message);
  } finally {
    busy = false;
  }
}

const click = (id, handler) => el(id).addEventListener("click", () => perform(handler));

click("new", async () => {
  current = await request("/api/inspections", {});
  sessionStorage.setItem("parts-desk-case", current.id);
});
click("tag", async () => { current = await request(route("/fixtures"), { variant: "tag" }); });
click("fixture", async () => { current = await request(route("/fixtures"), { variant: el("variant").value }); });
click("confirm", async () => {
  const body = {};
  const part = el("fix-part").value.trim();
  const serial = el("fix-serial").value.trim();
  if (part) body.part = part;
  if (serial) body.serial = serial;
  current = await request(route(`/captures/${current.pending.id}/confirm`), body);
  el("fix-part").value = "";
  el("fix-serial").value = "";
});
click("retake", async () => { current = await request(route(`/captures/${current.pending.id}/retake`), {}); });
click("close", async () => { current = await request(route("/close"), {}); });
click("call", async () => { await request(route("/facetime"), {}); current = await request(route()); });
click("end-call", async () => { await request(route("/facetime/end"), {}); });
click("capture", async () => { current = await request(route("/capture-tag"), {}); });
click("expect-tag", async () => { current = await request(route("/expect-photo"), { kind: "tag" }); });
click("expect-cert", async () => { current = await request(route("/expect-photo"), { kind: "certificate" }); });

click("export", async () => {
  const response = await fetch(route("/report"), { headers: auth(), cache: "no-store" });
  if (!response.ok) throw new Error("The report could not be exported.");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = `parts-desk-${current.id}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

function connectUpload(button, input, kind) {
  el(button).addEventListener("click", () => el(input).click());
  el(input).addEventListener("change", () => {
    const file = el(input).files[0];
    el(input).value = "";
    if (!file) return;
    perform(async () => {
      const body = new FormData();
      body.append("photo", file);
      current = await request(route(`/uploads/${kind}`), body);
    });
  });
}
connectUpload("tag-upload", "tag-file", "tag");
connectUpload("cert-upload", "cert-file", "certificate");

async function restore() {
  const key = sessionStorage.getItem("parts-desk-case");
  if (key) current = await request(`/api/inspections/${encodeURIComponent(key)}`);
}
el("token").addEventListener("change", () => perform(restore));
perform(restore);

async function refreshPreview() {
  const response = await fetch(route("/preview"), { headers: auth(), cache: "no-store" });
  const blob = response.status === 200 ? await response.blob() : null;
  if (cameraUrl) URL.revokeObjectURL(cameraUrl);
  cameraUrl = blob ? URL.createObjectURL(blob) : null;
  el("camera").hidden = !cameraUrl;
  if (cameraUrl) el("camera").src = cameraUrl;
  else el("camera").removeAttribute("src");
}

async function poll() {
  if (!busy && current) {
    const id = current.id;
    try {
      const update = await request(route(`/events?after=${current.last_seq}`));
      if (!busy && current?.id === id && update.events.length) {
        current = await request(route());
        render();
      }
      if (current?.mode === "live") await refreshPreview();
    } catch {
      // Keep polling; the next tick reloads from the latest snapshot.
    }
  }
  setTimeout(poll, POLL_MS);
}
poll();
