const workspaceDB = new Promise((resolve, reject) => {
  const request = indexedDB.open("parts-desk-public", 1);
  request.onupgradeneeded = () => request.result.createObjectStore("cases", {keyPath:"id"});
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(new Error("Enable browser storage to keep cases."));
});

async function caseStore(value) {
  const db = await workspaceDB;
  return new Promise((resolve, reject) => {
    const tx = db.transaction("cases", value ? "readwrite" : "readonly");
    const req = value ? tx.objectStore("cases").put(value) : tx.objectStore("cases").get(localStorage.getItem("parts-desk-case"));
    let result;
    req.onsuccess = () => {result = req.result;};
    tx.oncomplete = () => resolve(value || result);
    tx.onerror = () => reject(new Error("Browser storage is unavailable. Export the case."));
  });
}

async function workspace(path, body) {
  if (path === "/api/cases") return caseStore({
    id:crypto.randomUUID(), mode:"drill", state:"Awaiting evidence", events:[],
    identifier_result:"Awaiting evidence", issuer_template:"pending", rule:"PD-GE-FOOTER-1",
    faa_scope:"GE 1856M94P01 bushings sold by AOG Technics to TAP Maintenance & Engineering. Supplier and buyer are unverified in this exercise.",
  });
  const record = await caseStore();
  if (!record || !path.startsWith("/api/cases/"+record.id)) throw new Error("Start a new inspection.");
  if (body === undefined) return record;
  let kind, file, source;
  if (path.endsWith("/fixture")) {
    kind = body.variant === "tag" ? "tag" : "certificate";
    const response = await fetch("/static/fixtures/"+body.variant+".png");
    if (!response.ok) throw new Error("Fixture unavailable.");
    file = await response.blob();
    source = "Training fixture";
  } else if (body instanceof FormData) {
    kind = path.endsWith("/tag") ? "tag" : "certificate";
    file = body.get("photo");
    source = "Uploaded photo";
  } else throw new Error("Use the public inspection workflow.");
  if (file.size > 4*1024*1024) throw new Error("Use a photo under 4 MB.");
  document.getElementById("status").textContent = "Reading image…";
  const response = await fetch("/api/analyze/"+kind,{
    method:"POST",headers:{"Content-Type":"application/octet-stream"},body:file,
  });
  const result = await response.json().catch(()=>({}));
  if (!response.ok) throw new Error(result.detail || "Image analysis failed. Try again.");
  record[kind] = {...result,id:crypto.randomUUID(),source};
  record.events.push({seq:record.events.length+1,text:source+" · "+(kind==="tag"?"Read serial "+result.serial:result.reason)});
  const tag=record.tag, cert=record.certificate;
  record.identifier_result=tag && cert && cert.part && cert.serial
    ? tag.part===cert.part && tag.serial===cert.serial ? "Match":"Mismatch"
    : cert ? "Unresolved":"Awaiting evidence";
  const scope=tag?.part==="1856M94P01" && cert?.part==="1856M94P01";
  const status=cert?.status || "pending";
  record.issuer_template=scope || ["pending","retake","unresolved"].includes(status) ? status:"scope_pending";
  record.state=record.identifier_result==="Mismatch" || (scope && status==="missing")
    ? "Inspector review needed"
    : record.identifier_result==="Match" && scope && status==="present" ? "Checks recorded"
    : cert ? "Retake or verify evidence":"Awaiting evidence";
  record.issuer_draft=scope && status==="missing" && cert.serial
    ? "Please verify the training release for part "+cert.part+", serial "+cert.serial+
      ". The expected User/Installer Responsibilities section is absent in a visible region. Evidence SHA-256 "+cert.digest+". For inspector review."
    : null;
  document.getElementById("status").textContent="";
  return caseStore(record);
}
