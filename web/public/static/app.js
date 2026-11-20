const el = (id) => document.getElementById(id);
let current;
let busy = false;
let footerKey;
let footerUrl;
let cameraUrl;

function auth() {
  const token = el("token").value.trim();
  return token ? {Authorization: "Bearer " + token} : {};
}

async function request(path, body) { return workspace(path, body); }

const route = (tail = "") => "/api/cases/" + current.id + tail;
const put = (id, text = "—") => { el(id).textContent = text ?? "—"; };

function element(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

const labels = {
  pending: "Awaiting photo", present: "Section present", missing: "Verify issuer",
  retake: "Retake photo", unresolved: "Review footer", scope_pending: "Scope unconfirmed",
};

async function replaceFooter(cert) {
  const marker = cert?.footer ? current.id + "/" + cert.id : undefined;
  if (marker === footerKey) return;
  footerKey = marker;
  el("crop-wrap").hidden = true;
  el("crop").removeAttribute("src");
  if (footerUrl) URL.revokeObjectURL(footerUrl);
  footerUrl = undefined;
  if (!marker) return;
  try {
    footerUrl = "data:image/png;base64," + cert.footer;
    el("crop").src = footerUrl;
    el("crop-wrap").hidden = false;
  } catch {
    if (footerKey === marker) footerKey = undefined;
  }
}

function render() {
  el("desk").hidden = false;
  const {tag,certificate:cert} = current;
  put("case", "CASE " + current.id.toUpperCase());
  put("result", current.state);
  put("identifiers", current.identifier_result);
  put("template", labels[current.issuer_template]);
  el("identifiers").toggleAttribute("data-warning", current.identifier_result === "Mismatch");
  el("template").toggleAttribute("data-warning", ["missing","retake","unresolved"].includes(current.issuer_template));
  put("tag-source", tag?.source ?? "");
  put("photo-source", cert?.source ?? "");
  put("tag-part", tag?.part);
  put("tag-serial", tag?.serial);
  put("form-part", cert?.part);
  put("form-serial", cert?.serial);
  put("description", cert?.description ?? "");
  put("scope", current.faa_scope);
  put("rule", current.rule);
  put("evidence-reason", cert?.reason ?? "Load a certificate photo to inspect its expected footer.");
  put("digest", cert ? "Original SHA-256 " + cert.digest : "");
  el("raw").hidden = !cert;
  el("lines").replaceChildren(...(cert?.observations ?? []).map((line) => element("li", line.text)));
  el("draft-box").hidden = !current.issuer_draft;
  put("draft", current.issuer_draft ?? "");
  el("events").replaceChildren(...current.events.slice().reverse().map((entry) => element("li", entry.text)));
  el("call-tools").hidden = current.mode !== "live";
  el("expect-photo").hidden = current.mode !== "live";
  replaceFooter(cert);
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

function click(id, handler) {
  el(id).addEventListener("click", () => perform(handler));
}

click("new", async () => {
  current = await request("/api/cases", {});
  localStorage.setItem("parts-desk-case", current.id);
});
click("tag", async () => { current = await request(route("/fixture"), {variant:"tag"}); });
click("fixture", async () => { current = await request(route("/fixture"), {variant:el("variant").value}); });
click("call", async () => {
  await request(route("/facetime"), {});
  current = await request(route());
});
click("capture", async () => { current = await request(route("/capture-tag"), {}); });
click("expect-tag", async () => { current = await request(route("/expect-photo"), {kind:"tag"}); });
click("expect-photo", async () => { current = await request(route("/expect-photo"), {kind:"certificate"}); });

function connectUpload(button, input, kind) {
  el(button).addEventListener("click", () => el(input).click());
  el(input).addEventListener("change", () => {
    const file = el(input).files[0];
    el(input).value = "";
    if (!file) return;
    perform(async () => {
      const body = new FormData();
      body.append("photo", file);
      current = await request(route("/upload/" + kind), body);
    });
  });
}
connectUpload("tag-upload", "tag-file", "tag");
connectUpload("photo-upload", "photo-file", "certificate");

click("export", async () => {
  const snapshot = await request(route());
  const blob = new Blob([JSON.stringify(snapshot, null, 2) + "\n"], {type:"application/json"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "parts-desk-" + snapshot.id + ".json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

async function load() {
  const key = localStorage.getItem("parts-desk-case");
  if (key) current = await request("/api/cases/" + encodeURIComponent(key));
}
el("token").addEventListener("change", () => perform(load));
perform(load);

async function liveUpdate() {
  if (!busy && current?.mode === "live") {
    const id = current.id;
    const previous = current.events.at(-1)?.seq;
    try {
      const result = await request(route());
      if (!busy && current.id === id && current.events.at(-1)?.seq === previous &&
          result.events.at(-1)?.seq !== previous) {
        current = result;
        render();
      }
      const frame = await fetch("/api/cases/" + id + "/preview", {headers:auth(),cache:"no-store"});
      const blob = frame.ok && frame.status !== 204 ? await frame.blob() : null;
      if (current.id === id) {
        if (cameraUrl) URL.revokeObjectURL(cameraUrl);
        cameraUrl = blob ? URL.createObjectURL(blob) : undefined;
        const camera = el("camera");
        camera.hidden = !cameraUrl;
        if (cameraUrl) camera.src = cameraUrl;
        else camera.removeAttribute("src");
      }
    } catch {}
  }
  setTimeout(liveUpdate, 1200);
}
liveUpdate();
