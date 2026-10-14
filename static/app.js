const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// Internal agent names -> names shown to the user.
const DISPLAY = {
  "Lead Advisor": "Lead Advisor",
  "Landlord Investigator": "Ownership Review",
  "Neighborhood Scout": "Property & Area Records",
  "Lease Reviewer": "Lease Compliance",
  "Intake": "Intake",
  "Listing Analyst": "Listing Analyst",
};
const shown = (agent) => DISPLAY[agent] || agent;

const TOOL_WORDS = {
  ask_landlord_investigator: "opened the ownership review",
  ask_neighborhood_scout: "opened the property and area review",
  ask_lease_reviewer: "opened the lease compliance review",
  get_property_records: "pulled the town assessor records",
  get_owner_portfolio: "counted the owner's Connecticut properties",
  search_business_registry: "searched the Connecticut business registry",
  get_company_principals: "looked up the people behind the company",
  get_311_reports: "pulled nearby 311 reports",
  get_eviction_filings: "checked eviction filings in the census tract",
  get_flood_zone: "checked the FEMA flood map",
  read_lease: "read the lease",
  check_security_deposit: "checked the security deposit against CT law",
  check_late_fee: "checked the late fee against CT law",
  check_upfront_fees: "checked an up-front fee against CT law",
  read_listing: "read the listing",
  scan_scam_patterns: "scanned for rental-scam warning signs",
  lookup_property: "looked up the owner of record",
  compare_names: "compared the listing's name with the owner of record",
};

// ------------------------------------------------------------------ form

let samples = [];

async function loadSamples() {
  samples = await (await fetch("/api/samples")).json();
  $("#sample-lease").innerHTML = samples
    .map((s) => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join("");
  updateSampleLink();
}

function updateSampleLink() {
  $("#sample-link").href = `/samples/${encodeURIComponent($("#sample-lease").value)}`;
}

document.querySelectorAll(".chip").forEach((b) =>
  b.addEventListener("click", () => { $("#address").value = b.dataset.address; $("#address").focus(); }));

document.querySelectorAll('input[name="lease_mode"]').forEach((r) =>
  r.addEventListener("change", () => {
    const mode = document.querySelector('input[name="lease_mode"]:checked').value;
    $("#upload-row").hidden = mode !== "upload";
    $("#sample-row").hidden = mode !== "sample";
  }));
$("#sample-lease").addEventListener("change", updateSampleLink);

$("#check-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#form-error");
  err.hidden = true;
  const mode = document.querySelector('input[name="lease_mode"]:checked').value;
  const data = new FormData();
  data.append("address", $("#address").value.trim());
  data.append("language", $("#language").value);
  if (mode === "upload") {
    const file = $("#lease-file").files[0];
    if (!file) { showFormError("Please choose your lease file, or pick “No lease yet”."); return; }
    data.append("lease", file);
  } else if (mode === "sample") {
    data.append("sample_lease", $("#sample-lease").value);
  }

  $("#submit").disabled = true;
  $("#submit").textContent = "Starting…";
  try {
    const resp = await fetch("/api/check", { method: "POST", body: data });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || "Something went wrong.");
    startProgress(mode !== "none");
    poll(body.job_id, 0);
  } catch (ex) {
    showFormError(ex.message);
    resetButton();
  }
});

function showFormError(msg) {
  const err = $("#form-error");
  err.textContent = msg;
  err.hidden = false;
}

function resetButton() {
  $("#submit").disabled = false;
  $("#submit").textContent = "Begin verification";
  $("#compare-submit").disabled = false;
  $("#compare-submit").textContent = "Compare apartments";
  $("#listing-submit").disabled = false;
  $("#listing-submit").textContent = "Check this listing";
}

// -------------------------------------------------------------- progress

function startProgress(hasLease) {
  $("#report").hidden = true;
  $("#compare-result").hidden = true;
  $("#team").hidden = false;
  $("#compare-team").hidden = true;
  $("#progress-card").hidden = false;
  $("#activity").innerHTML = "";
  $("#progress-status").textContent = "Getting started…";
  document.querySelectorAll("#team li").forEach((li) => {
    li.className = "";
    li.querySelector("em").textContent = "Pending";
  });
  if (!hasLease) {
    const li = document.querySelector('#team li[data-agent="Lease Reviewer"]');
    li.className = "skipped";
    li.querySelector("em").textContent = "Not requested";
  }
  $("#progress-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function setAgent(agent, state, label) {
  const li = document.querySelector(`#team li[data-agent="${agent}"]`);
  if (!li) return;
  li.className = state;
  li.querySelector("em").textContent = label;
}

function addActivity(html, cls = "") {
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = html;
  $("#activity").appendChild(li);
  $("#activity").scrollTop = $("#activity").scrollHeight;
}

function handleEvent(ev) {
  switch (ev.kind) {
    case "status":
      $("#progress-status").textContent = ev.message.startsWith("Matched ")
        ? `Matched ${prettyAddress(ev.message.slice(8))}` : ev.message;
      break;
    case "agent_start":
      setAgent(ev.agent, "working", "In progress");
      break;
    case "agent_done":
      setAgent(ev.agent, "done", "Complete");
      break;
    case "agent_error":
      setAgent(ev.agent, "done", "Incomplete");
      addActivity(`<b>${esc(shown(ev.agent))}</b> could not finish.`, "safety");
      break;
    case "model_response":
      (ev.tool_calls || []).forEach((c) => {
        const words = TOOL_WORDS[c.tool] || `used ${esc(c.tool)}`;
        const arg = c.args && (c.args.company_name || c.args.owner_name);
        addActivity(`<b>${esc(shown(ev.agent))}</b> ${words}${arg ? `: ${esc(arg)}` : ""}`);
      });
      break;
    case "safety":
      addActivity(`<b>Safeguard:</b> ${esc(ev.detail)}`, "safety");
      break;
    case "sub_started":
      setCompareRow(ev.label, "working", "In progress");
      addActivity(`<b>Apartment ${esc(ev.label)}</b> verification started: ${esc(ev.address)}`);
      break;
    case "sub_done":
      setCompareRow(ev.label, "done", ev.status === "done" ? (ev.verdict || "Complete") : "Could not check");
      addActivity(`<b>Apartment ${esc(ev.label)}</b> ${ev.status === "done" ? `finished: ${esc(ev.verdict)}` : `could not be checked: ${esc(ev.error || "")}`}`,
                  ev.status === "done" ? "" : "safety");
      break;
  }
}

async function poll(jobId, since) {
  let next = since;
  try {
    const resp = await fetch(`/api/jobs/${jobId}?since=${since}`);
    const body = await resp.json();
    body.events.forEach(handleEvent);
    next = body.next;
    if (body.status === "running") {
      setTimeout(() => poll(jobId, next), 1000);
      return;
    }
    resetButton();
    if (body.status === "done") {
      $("#progress-status").textContent = `Completed in ${body.result.seconds} seconds`;
      if (body.result.mode === "compare") renderCompare(body.result, jobId);
      else if (body.result.mode === "listing") renderListing(body.result, jobId);
      else renderReport(body.result, jobId);
    } else {
      $("#progress-status").textContent = "Verification stopped";
      // If no agent ever started (e.g. the address was refused), the team list is just noise.
      if (!document.querySelector("#team li.working, #team li.done")) $("#progress-card").hidden = true;
      showFormError(body.error || "Something went wrong.");
      $("#form-card").scrollIntoView({ behavior: "smooth" });
    }
  } catch (ex) {
    setTimeout(() => poll(jobId, next), 2000);
  }
}

// ---------------------------------------------------------------- report

const VERDICT_CLASS = { "Looks good": "good", "Proceed with caution": "caution", "Serious concerns": "serious" };
const ORDER = { red: 0, yellow: 1, green: 2 };

// "224 WOOSTER ST, NEW HAVEN, CT, 06511" -> "224 Wooster St, New Haven, CT 06511"
function prettyAddress(raw) {
  const parts = String(raw).split(",").map((p) => p.trim());
  const zip = /^\d{5}$/.test(parts[parts.length - 1]) ? parts.pop() : "";
  const state = parts.length > 2 ? parts.pop() : "";
  const words = parts.join(", ").toLowerCase().replace(/\b([a-z])/g, (c) => c.toUpperCase());
  return [words, [state, zip].filter(Boolean).join(" ")].filter(Boolean).join(", ");
}

// Citations are shown under each finding, so drop raw IDs like "[CT-47a-21-b]" or "(E3)" from the prose.
function stripIds(text) {
  return String(text).replace(/\s*[\[(](?:CT-[\w-]+|E\d+|LEASE)(?:[,;\s]+(?:CT-[\w-]+|E\d+|LEASE))*[\])]/g, "");
}

function n(x) { return Number(x || 0).toLocaleString(); }

// Collapse many statute and calculator entries into single readable lines.
function groupedSources(evidence, T) {
  const statutes = [], calculators = [], other = new Set();
  evidence.forEach((e) => {
    if (e.id === "LEASE") other.add(T.yourLease);
    else if (e.id.startsWith("CT-")) statutes.push(e.source.split(":")[0].replace("Conn. Gen. Stat. ", ""));
    else if (/calculator|check \(/i.test(e.source)) calculators.push(e.source.split(" (")[0]);
    else other.add(e.source.replace(/ principals for business \w+$/, " principals"));
  });
  const out = [...other];
  if (statutes.length) out.push(`Connecticut General Statutes ${[...new Set(statutes)].join(", ")}`);
  if (calculators.length) out.push(`Built-in checks: ${[...new Set(calculators)].join(", ")}`);
  return out;
}

function findingsHtml(findings, evidence, T) {
  return findings.map((f) => {
    const seen = new Set();
    const srcs = f.evidence_ids.filter((id) => {
      const label = evidence[id] ? evidence[id].source : id;  // show each source once
      if (seen.has(label)) return false;
      seen.add(label);
      return true;
    }).map((id) => {
      const e = evidence[id];
      if (!e) return `<span>${esc(id)}</span>`;
      const label = id === "LEASE" ? T.yourLease : id === "LISTING" ? T.category.listing
        : e.source.replace(/ principals for business \w+$/, " principals");
      return e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener" title="${esc(id)}">${esc(label)}</a>`
                   : `<span title="${esc(id)}">${esc(label)}</span>`;
    }).join("");
    return `<article class="finding ${esc(f.severity)}">
      <div class="label">
        <span class="tag ${esc(f.severity)}">${T.severity[f.severity] || esc(f.severity)}</span><br>
        <span class="tag cat">${T.category[f.category] || esc(f.category)}</span>
      </div>
      <div>
        <h3>${esc(stripIds(f.title))}</h3>
        <p>${esc(stripIds(f.detail))}</p>
        <div class="sources">${T.sources} ${srcs}</div>
      </div>
    </article>`;
  }).join("");
}

function renderReport(result, jobId, { scroll = true } = {}) {
  const T = I18N[result.language] || I18N.en;
  const r = result.report;
  const evidence = Object.fromEntries((result.evidence || []).map((e) => [e.id, e]));
  const findings = [...r.findings].sort((a, b) => ORDER[a.severity] - ORDER[b.severity]);

  const findingHtml = findingsHtml(findings, evidence, T);

  const gaps = [...(r.gaps || [])];
  const safety = result.safety_events || [];

  const when = new Date().toLocaleString(T.locale, { dateStyle: "long", timeStyle: "short" });

  $("#report").innerHTML = sectionNav([["r-summary", T.summaryTab], ["r-findings", T.findings], ["r-questions", T.questions],
                                       ...($("#report") && negotiableIssues(result).length ? [["coach", T.coachUI.title]] : []),
                                       ["r-limits", T.limits], ["r-method", T.method]]) + `
    <section class="card" id="r-summary">
      <div class="report-head">
        <div>
          <p class="eyebrow">${T.eyebrow}</p>
          <div class="report-address">${esc(prettyAddress(result.address.matched_address))}</div>
        </div>
        <div class="report-meta">
          ${T.reportNo} <b>${esc(String(jobId).toUpperCase())}</b><br>
          ${T.issued} <b>${esc(when)}</b><br>
          ${T.leaseReviewed}: <b>${result.has_lease ? T.yes : T.no}</b>
        </div>
      </div>
      ${result.sample ? `<p class="sample-banner"><b>Sample report.</b> Saved from a real check of this address with a sample lease on ${esc(result.sample.when)}. Scroll down to try the negotiation coach, or run your own check above.</p>` : ""}
      <span class="verdict ${VERDICT_CLASS[r.verdict] || "caution"}">${esc(T.verdict[r.verdict] || r.verdict)}</span>
      <p class="headline">${esc(r.headline)}</p>
      <p class="summary">${esc(r.summary)}</p>
    </section>

    <section class="card" id="r-findings">
      <div class="card-head"><h2>${T.findings}</h2><p>${T.items(findings.length)}</p></div>
      ${findingHtml || `<p class="muted">${T.none}</p>`}
    </section>

    <section class="card" id="r-questions">
      <div class="card-head"><h2>${T.questions}</h2><p>${T.askBefore}</p></div>
      <ol class="questions">${r.questions_for_landlord.map((q) => `<li>${esc(q)}</li>`).join("")}</ol>
    </section>

    ${coachCardHtml(result, T)}

    <section class="card" id="r-limits">
      <div class="registry">
        <h3>${T.registryTitle}</h3>
        <p>${T.registryText}</p>
        <p><a href="${esc(result.registry_links.ct_registry)}" target="_blank" rel="noopener">${T.ctReg}</a>
           &nbsp;·&nbsp; <a href="${esc(result.registry_links.national_registry)}" target="_blank" rel="noopener">${T.natReg}</a></p>
      </div>
      ${gaps.length ? `<h3 style="margin:28px 0 8px;color:var(--navy)">${T.limits}</h3><ul>${gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : ""}
    </section>

    <section class="card" id="r-method">
      <details class="behind">
        <summary>${T.method}</summary>
        <p>${esc(T.methodText)}</p>
        <h3 style="color:var(--navy);font-size:19px">${esc(T.sourcesUsed)}</h3>
        <ul class="safety-list">${groupedSources(result.evidence || [], T).map((src) => `<li>${esc(src)}</li>`).join("")}</ul>
        <h3 style="color:var(--navy);font-size:19px">${esc(T.safeguards)}</h3>
        ${safety.length ? `<ul class="safety-list">${safety.map((s) => `<li>${esc(s.detail)}</li>`).join("")}</ul>`
                        : `<p class="small">${esc(T.noSafeguards)}</p>`}
      </details>
    </section>

    <div class="report-actions">
      <button class="secondary" onclick="window.print()">${T.print}</button>
      <button class="primary" onclick="window.scrollTo({top:0,behavior:'smooth'})">${T.newCheck}</button>
    </div>
  `;
  $("#report").hidden = false;
  setupCoach(result, jobId, T);
  watchSections($("#report"));
  if (scroll) $("#report").scrollIntoView({ behavior: "smooth", block: "start" });
}

loadSamples();
const prefill = new URLSearchParams(location.search).get("address");
if (prefill) $("#address").value = prefill;


// ------------------------------------------------------------ shortlist mode

const LETTERS = "ABCDE";

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => {
    x.classList.toggle("active", x === t);
    x.setAttribute("aria-selected", x === t ? "true" : "false");
  });
  $("#check-form").hidden = t.dataset.tab !== "single";
  $("#compare-form").hidden = t.dataset.tab !== "compare";
  $("#listing-form").hidden = t.dataset.tab !== "listing";
  $("#form-error").hidden = true;
}));

$("#compare-language").innerHTML = $("#language").innerHTML;
$("#listing-language").innerHTML = $("#language").innerHTML;

function relabelRows() {
  document.querySelectorAll("#compare-rows .compare-row").forEach((row, i) => {
    row.querySelector(".letter").textContent = LETTERS[i];
  });
  const n = document.querySelectorAll("#compare-rows .compare-row").length;
  $("#add-row").hidden = n >= 5;
  document.querySelectorAll("#compare-rows .remove").forEach((b) => { b.hidden = n <= 2; });
}

function addRow(address = "", rent = "") {
  const row = document.createElement("div");
  row.className = "compare-row";
  row.innerHTML = `<span class="letter"></span>
    <input type="text" class="addr" placeholder="Street address, town, CT" maxlength="200" aria-label="Apartment address" value="${esc(address)}">
    <input type="text" class="rent" placeholder="$ per month" inputmode="decimal" maxlength="9" aria-label="Monthly rent (optional)" value="${esc(rent)}">
    <button type="button" class="remove" title="Remove">×</button>`;
  row.querySelector(".remove").addEventListener("click", () => { row.remove(); relabelRows(); });
  $("#compare-rows").appendChild(row);
  relabelRows();
}

addRow("224 Wooster St, New Haven, CT", "1850");
addRow("31 Chapel St, New Haven, CT", "1500");
addRow("114 Prospect St, East Hartford, CT", "1400");
$("#add-row").addEventListener("click", () => addRow());

$("#compare-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#form-error").hidden = true;
  const rows = [...document.querySelectorAll("#compare-rows .compare-row")]
    .map((r) => ({ address: r.querySelector(".addr").value.trim(), rent: r.querySelector(".rent").value.trim() }))
    .filter((r) => r.address);
  if (rows.length < 2) { showFormError("Enter at least two addresses to compare."); return; }
  const data = new FormData();
  rows.forEach((r) => { data.append("address", r.address); data.append("rent", r.rent); });
  data.append("language", $("#compare-language").value);
  $("#compare-submit").disabled = true;
  $("#compare-submit").textContent = "Starting…";
  try {
    const resp = await fetch("/api/compare", { method: "POST", body: data });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || "Something went wrong.");
    startCompareProgress(rows);
    poll(body.job_id, 0);
  } catch (ex) {
    showFormError(ex.message);
    resetButton();
  }
});

function startCompareProgress(rows) {
  $("#report").hidden = true;
  $("#compare-result").hidden = true;
  $("#team").hidden = true;
  $("#compare-team").hidden = false;
  $("#compare-team").innerHTML = rows.map((r, i) => `<li data-label="${LETTERS[i]}"><span class="dot"></span>
      <div><strong>Apartment ${LETTERS[i]}</strong><span>${esc(r.address)}${r.rent ? ` · $${esc(r.rent)}/mo` : ""}</span></div><em>Pending</em></li>`).join("")
    + `<li data-agent="Comparison Advisor"><span class="dot"></span><div><strong>Comparison Advisor</strong><span>Ranks the shortlist from the verified reports</span></div><em>Pending</em></li>`;
  $("#activity").innerHTML = "";
  $("#progress-status").textContent = "Getting started…";
  $("#progress-card").hidden = false;
  $("#progress-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function setCompareRow(label, state, text) {
  const li = document.querySelector(`#compare-team li[data-label="${label}"]`);
  if (!li) return;
  li.className = state;
  li.querySelector("em").textContent = text;
}

let compareMap = null;

function renderCompare(result, jobId) {
  const T = I18N[result.language] || I18N.en;
  const c = result.comparison;
  const byLabel = Object.fromEntries(result.apartments.map((a) => [a.label, a]));
  const vclass = (a) => (a.report ? (VERDICT_CLASS[a.report.verdict] || "caution") : "none");
  const counts = (a) => {
    const f = (a.report && a.report.findings) || [];
    return ["red", "yellow", "green"].map((s) => f.filter((x) => x.severity === s).length);
  };

  const rows = c.ranking.map((r) => {
    const a = byLabel[r.label];
    const [red, yellow, green] = counts(a);
    return `<div class="rank-row">
      <div class="rank-num">${r.rank}</div>
      <div>
        <h3>${esc(r.label)} · ${esc(prettyAddress(a.matched_address || a.address))}</h3>
        <div class="rank-meta">${T.riskScore}: <b>${a.risk_points}</b> · ${a.rent ? `${T.rent}: $${Number(a.rent).toLocaleString("en-US")} · ` : ""}
          <span class="verdict ${vclass(a)}" style="margin:0;padding:3px 9px;font-size:10.5px">${esc(T.verdict[a.report.verdict] || a.report.verdict)}</span>
          · ${red} ${esc(T.severity.red)} · ${yellow} ${esc(T.severity.yellow)} · ${green} ${esc(T.severity.green)}</div>
        <p style="margin:0 0 10px">${esc(stripIds(r.summary))}</p>
        <div class="pc">
          <div class="pro"><b>${T.strengths}</b><ul>${r.strengths.map((x) => `<li>${esc(stripIds(x))}</li>`).join("")}</ul></div>
          <div class="con"><b>${T.concerns}</b><ul>${r.concerns.map((x) => `<li>${esc(stripIds(x))}</li>`).join("")}</ul></div>
        </div>
        <p style="margin:12px 0 0"><button class="secondary view-report" data-job="${esc(a.job_id)}">${T.viewReport}</button></p>
      </div>
    </div>`;
  }).join("");

  const failed = result.apartments.filter((a) => a.status !== "done");

  $("#compare-result").innerHTML = sectionNav([["c-ranking", T.rankingTab], ["c-rec", T.recommendation]]) + `
    <section class="card" id="c-ranking">
      <div class="card-head"><h2>${T.comparison}</h2></div>
      <div id="map"></div>
      ${rows}
      <p class="small" style="margin-top:14px">${esc(T.riskScore)}: ${esc(result.scoring)}</p>
      ${failed.length ? `<p class="small">${T.notChecked}: ${failed.map((a) => `${esc(a.label)} (${esc(a.address)})`).join(", ")}</p>` : ""}
    </section>
    <section class="card" id="c-rec">
      <div class="card-head"><h2>${T.recommendation}</h2></div>
      <p>${esc(stripIds(c.recommendation))}</p>
      <h3 style="margin:22px 0 6px;color:var(--navy)">${T.tradeoffs}</h3>
      <p>${esc(stripIds(c.tradeoffs))}</p>
    </section>`;
  $("#compare-result").hidden = false;
  $("#report").hidden = true;
  watchSections($("#compare-result"));

  // Interactive map: one pin per apartment, colored by verdict.
  if (compareMap) { compareMap.remove(); compareMap = null; }
  const located = result.apartments.filter((a) => a.lat && a.lng);
  if (window.L && located.length) {
    compareMap = L.map("map", { scrollWheelZoom: false }).setView([41.6, -72.7], 9);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(compareMap);
    const rankOf = Object.fromEntries(c.ranking.map((r) => [r.label, r.rank]));
    located.forEach((a) => {
      const icon = L.divIcon({ className: "", html: `<div class="pin ${vclass(a)}">${esc(a.label)}</div>`, iconSize: [30, 30], iconAnchor: [15, 15] });
      L.marker([a.lat, a.lng], { icon }).addTo(compareMap).bindPopup(
        `<b>${esc(a.label)} · ${esc(prettyAddress(a.matched_address || a.address))}</b><br>` +
        (rankOf[a.label] ? `${T.rank} ${rankOf[a.label]} · ` : "") + `${esc(a.report ? (T.verdict[a.report.verdict] || a.report.verdict) : T.notChecked)}<br>` +
        (a.report ? `${esc(a.report.headline)}<br>` : "") +
        `<a href="#" class="map-report" data-job="${esc(a.job_id)}">${T.viewReport}</a>`);
    });
    const fit = () => { compareMap.invalidateSize(); compareMap.fitBounds(L.latLngBounds(located.map((a) => [a.lat, a.lng])), { padding: [40, 40], maxZoom: 15 }); };
    fit(); setTimeout(fit, 300);  // refit once the page has finished laying out
  }

  $("#compare-result").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function openSubReport(jobId) {
  const resp = await fetch(`/api/jobs/${jobId}`);
  const body = await resp.json();
  if (body.result && body.result.report) renderReport(body.result, jobId);
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest(".view-report, .map-report");
  if (!btn) return;
  e.preventDefault();
  openSubReport(btn.dataset.job);
});


// ---------------------------------------------------------- negotiation coach

const MAX_TURNS = 12;

// Same selection as the server (app/coach.py): red and yellow items, lease first, at most 8.
function negotiableIssues(result) {
  const order = { red: 0, yellow: 1 };
  return result.report.findings
    .filter((f) => f.severity in order)
    .map((f, i) => ({ f, i }))
    .sort((a, b) => (a.f.category !== "lease") - (b.f.category !== "lease") || order[a.f.severity] - order[b.f.severity] || a.i - b.i)
    .slice(0, 8)
    .map(({ f }, k) => ({ id: k + 1, severity: f.severity, title: f.title }));
}

function coachCardHtml(result, T) {
  const C = T.coachUI;
  if (!negotiableIssues(result).length) return "";
  const personas = Object.entries(C.personas).map(([k, [name, desc]], i) =>
    `<label class="persona"><input type="radio" name="persona" value="${k}" ${i === 0 ? "checked" : ""}>
       <b>${esc(name)}</b><span>${esc(desc)}</span></label>`).join("");
  return `<section class="card" id="coach">
    <div class="card-head"><h2>${esc(C.title)}</h2></div>
    <p>${esc(C.intro)}</p>
    <div class="coach-actions">
      <button class="secondary" id="btn-letter">${esc(C.draftLetter)}</button>
      <button class="secondary" id="btn-practice">${esc(C.practice)}</button>
    </div>
    <div id="letter-box" hidden></div>
    <div id="practice-box" hidden>
      <p class="field-label" style="margin-top:22px">${esc(C.choose)}</p>
      <div class="personas">${personas}</div>
      <div class="practice-grid">
        <div>
          <div id="chat" class="chat"></div>
          <form id="chat-form" class="chat-form">
            <textarea id="chat-input" rows="2" maxlength="800" placeholder="${esc(C.placeholder)}"></textarea>
            <button class="primary" type="submit" id="chat-send">${esc(C.send)}</button>
          </form>
          <p class="small">${esc(C.note)}</p>
        </div>
        <aside class="scoreboard">
          <p class="field-label">${esc(C.won)}: <span id="won-count">0</span>/<span id="won-total">0</span></p>
          <ol id="issue-list" class="issue-list"></ol>
          <p class="small">${esc(C.turnsLeft)}: <b id="turns-left">${MAX_TURNS}</b></p>
          <button class="secondary" id="btn-restart" type="button">${esc(C.restart)}</button>
        </aside>
      </div>
    </div>
  </section>`;
}

function setupCoach(result, jobId, T) {
  const C = T && T.coachUI;
  if (!C || !$("#coach")) return;
  const issues = negotiableIssues(result);
  let history = [], won = new Set(), busy = false;

  const renderIssues = () => {
    $("#issue-list").innerHTML = issues.map((i) =>
      `<li class="${won.has(i.id) ? "won" : ""}"><span class="tag ${i.severity}" style="margin:0 6px 0 0">${won.has(i.id) ? "✓" : i.id}</span>${esc(stripIds(i.title))}</li>`).join("");
    $("#won-count").textContent = won.size;
    $("#won-total").textContent = issues.length;
    $("#turns-left").textContent = Math.max(0, MAX_TURNS - history.length / 2);
  };
  const say = (who, cls, text, extra = "") => {
    const div = document.createElement("div");
    div.className = `msg ${cls}`;
    div.innerHTML = `<b>${esc(who)}</b>${extra}<p>${esc(text)}</p>`;
    $("#chat").appendChild(div);
    $("#chat").scrollTop = $("#chat").scrollHeight;
  };
  const reset = () => { history = []; won = new Set(); $("#chat").innerHTML = ""; renderIssues(); };

  $("#btn-letter").addEventListener("click", async () => {
    const box = $("#letter-box");
    box.hidden = false;
    box.innerHTML = `<p class="muted" style="margin-top:18px">…</p>`;
    $("#btn-letter").disabled = true;
    try {
      const resp = await fetch(`/api/jobs/${jobId}/letter`, { method: "POST" });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail || "Error");
      box.innerHTML = `<div class="letter-card">
        <p class="small">${esc(C.letterNote)}</p>
        <div class="letter-paper">
          <div class="letter-subject"><span>Subject</span>${esc(body.subject)}</div>
          <p>${esc(body.greeting)}</p>
          <p>${esc(body.opening)}</p>
          <ol class="letter-items">${body.items.map((i) => `<li>
            <b>${esc(i.topic)}</b>
            <p>${esc(stripIds(i.request))}</p>
            ${i.statute ? `<span class="cite">${esc(i.statute)}</span>` : ""}</li>`).join("")}</ol>
          <p>${esc(body.closing)}</p>
          <p class="sign">${esc(body.sign_off)}<br>[Your name]</p>
        </div>
        <button class="secondary" id="btn-copy">${esc(C.copy)}</button></div>`;
      $("#btn-copy").addEventListener("click", async () => {
        await navigator.clipboard.writeText(body.plain_text);
        $("#btn-copy").textContent = C.copied;
      });
    } catch (ex) {
      box.innerHTML = `<p class="error">${esc(ex.message)}</p>`;
    } finally {
      $("#btn-letter").disabled = false;
    }
  });

  $("#btn-practice").addEventListener("click", () => {
    $("#practice-box").hidden = false;
    renderIssues();
    $("#chat-input").focus({ preventScroll: true });
  });
  $("#btn-restart").addEventListener("click", reset);
  document.querySelectorAll('input[name="persona"]').forEach((r) => r.addEventListener("change", reset));

  $("#chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = $("#chat-input").value.trim();
    if (!message || busy) return;
    if (history.length / 2 >= MAX_TURNS) return;
    busy = true;
    $("#chat-send").disabled = true;
    $("#chat-input").value = "";
    say(C.you, "you", message);
    const persona = document.querySelector('input[name="persona"]:checked').value;
    try {
      const resp = await fetch(`/api/jobs/${jobId}/practice`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ persona, history, message }),
      });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail || "Error");
      history.push({ role: "tenant", text: message }, { role: "landlord", text: body.reply });
      body.conceded.forEach((id) => won.add(id));
      say(C.landlord, "landlord", body.reply);
      say(C.coach, "coach", body.tip, ` <span class="stars">${"★".repeat(body.score)}${"☆".repeat(5 - body.score)}</span>`);
      renderIssues();
      if (won.size === issues.length) say(C.coach, "coach win", C.allWon);
    } catch (ex) {
      say("!", "coach", ex.message);
    } finally {
      busy = false;
      $("#chat-send").disabled = history.length / 2 >= MAX_TURNS;
    }
  });
}


// ---------------------------------------------------------------- listing mode

document.querySelectorAll("[data-listing]").forEach((b) => b.addEventListener("click", async () => {
  const resp = await fetch(`/api/sample-listing/${b.dataset.listing}`);
  $("#listing-text").value = (await resp.json()).text;
}));

$("#listing-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#form-error").hidden = true;
  const text = $("#listing-text").value.trim();
  if (text.length < 40) { showFormError("Paste the full listing text first."); return; }
  const data = new FormData();
  data.append("listing_text", text);
  data.append("language", $("#listing-language").value);
  $("#listing-submit").disabled = true;
  $("#listing-submit").textContent = "Starting…";
  try {
    const resp = await fetch("/api/listing", { method: "POST", body: data });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || "Something went wrong.");
    startProgress(false);
    $("#team").hidden = true;
    poll(body.job_id, 0);
  } catch (ex) {
    showFormError(ex.message);
    resetButton();
  }
});

function renderListing(result, jobId) {
  const T = I18N[result.language] || I18N.en;
  const L = T.listingUI;
  const r = result.report;
  const evidence = Object.fromEntries((result.evidence || []).map((e) => [e.id, e]));
  const findings = [...r.findings].sort((a, b) => ORDER[a.severity] - ORDER[b.severity]);
  const addr = result.address && result.address.matched_address;
  $("#compare-result").hidden = true;
  $("#report").innerHTML = sectionNav([["l-summary", T.summaryTab], ["l-findings", T.findings], ["l-next", L.nextSteps]]) + `
    <section class="card" id="l-summary">
      <div class="report-head"><div><p class="eyebrow">${esc(L.eyebrow)}</p>
        <div class="report-address">${addr ? esc(prettyAddress(addr)) : "—"}</div></div>
        <div class="report-meta">${T.reportNo} <b>${esc(String(jobId).toUpperCase())}</b></div></div>
      <span class="verdict risk-${esc(r.risk)}">${esc(L.risk[r.risk] || r.risk)}</span>
      <p class="headline">${esc(r.headline)}</p>
      <p class="summary">${esc(r.summary)}</p>
    </section>
    <section class="card" id="l-findings">
      <div class="card-head"><h2>${T.findings}</h2><p>${T.items(findings.length)}</p></div>
      ${findingsHtml(findings, evidence, T)}
    </section>
    <section class="card" id="l-next">
      <div class="card-head"><h2>${esc(L.nextSteps)}</h2></div>
      <ol class="questions">${r.next_steps.map((q) => `<li>${esc(stripIds(q))}</li>`).join("")}</ol>
      <div class="coach-actions">
        <a class="secondary" style="text-decoration:none;padding:11px 20px;border:1px solid #c9ced6" href="${esc(result.ftc_report_url)}" target="_blank" rel="noopener">${esc(L.ftc)}</a>
        ${addr ? `<button class="primary" id="btn-full-check">${esc(L.fullCheck)}</button>` : ""}
      </div>
    </section>`;
  $("#report").hidden = false;
  watchSections($("#report"));
  if (addr) $("#btn-full-check").addEventListener("click", () => {
    document.querySelector('.tab[data-tab="single"]').click();
    $("#address").value = prettyAddress(addr);
    $("#form-card").scrollIntoView({ behavior: "smooth" });
  });
  $("#report").scrollIntoView({ behavior: "smooth", block: "start" });
}


// ------------------------------------------------------------ first-visit help

async function openSample(thenCoach = false) {
  const resp = await fetch("/api/jobs/sample");
  if (!resp.ok) { showFormError("The sample report isn't available right now. Try running a check instead."); return; }
  const body = await resp.json();
  $("#progress-card").hidden = true;
  $("#compare-result").hidden = true;
  renderReport(body.result, "sample", { scroll: !thenCoach });
  if (thenCoach && $("#coach")) {
    $("#btn-practice").click();
    setTimeout(() => $("#coach").scrollIntoView({ behavior: scrollMode(), block: "start" }), 50);
  }
}

$("#see-sample").addEventListener("click", () => openSample(false));

// Feature cards in the hero and the sticky feature bar share one handler.
function markFeature(go) {
  document.querySelectorAll(".fb-tabs button").forEach((b) => b.classList.toggle("fb-active", b.dataset.go === go));
}

document.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => {
  const go = el.dataset.go;
  if (go === "org") { location.href = "/org"; return; }
  if (go === "sample") { openSample(false); return; }
  if (go === "coach") {
    markFeature("coach");
    // Use the report on screen if there is one with a coach; otherwise open the sample report's coach.
    if ($("#coach") && !$("#report").hidden) {
      $("#btn-practice").click();
      $("#coach").scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      openSample(true);
    }
    return;
  }
  markFeature(go);
  document.querySelector(`.tab[data-tab="${go}"]`).click();
  $("#form-card").scrollIntoView({ behavior: "smooth", block: "start" });
}));
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => markFeature(t.dataset.tab)));

// Links from other pages (e.g. /org) arrive as /?go=compare and open that feature.
const goParam = new URLSearchParams(location.search).get("go");
if (goParam) {
  const target = document.querySelector(`.fb-tabs [data-go="${CSS.escape(goParam)}"], .fb-sample[data-go="${CSS.escape(goParam)}"]`);
  if (target) setTimeout(() => target.click(), 300);
}


// ------------------------------------------------------------ section navigation

// A sticky bar of jump links at the top of a result, so nothing needs a long scroll.
function sectionNav(items) {
  return `<nav class="secnav" aria-label="Sections"><div>${items.map(([id, label]) =>
    `<a href="#${id}" data-sec="${id}">${esc(label)}</a>`).join("")}</div></nav>`;
}

// Smooth scrolling only runs in a visible tab; jump instantly otherwise so the target is still reached.
const scrollMode = () => (document.visibilityState === "visible" ? "smooth" : "auto");

let sectionObserver = null;
function watchSections(container) {
  const nav = container.querySelector(".secnav");
  if (!nav) return;
  nav.querySelectorAll("a").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    const target = document.getElementById(a.dataset.sec);
    if (target) target.scrollIntoView({ behavior: scrollMode(), block: "start" });
  }));
  if (sectionObserver) sectionObserver.disconnect();
  sectionObserver = new IntersectionObserver((entries) => {
    entries.filter((en) => en.isIntersecting).forEach((en) => {
      nav.querySelectorAll("a").forEach((a) => a.classList.toggle("active", a.dataset.sec === en.target.id));
    });
  }, { rootMargin: "-130px 0px -60% 0px" });
  nav.querySelectorAll("a").forEach((a) => { const t = document.getElementById(a.dataset.sec); if (t) sectionObserver.observe(t); });
}
