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
};
const shown = (agent) => DISPLAY[agent] || agent;

const TOOL_WORDS = {
  ask_landlord_investigator: "opened the ownership review",
  ask_neighborhood_scout: "opened the property and area review",
  ask_lease_reviewer: "opened the lease compliance review",
  get_property_records: "pulled the New Haven assessor records",
  get_owner_portfolio: "counted the owner's New Haven properties",
  search_business_registry: "searched the Connecticut business registry",
  get_company_principals: "looked up the people behind the company",
  get_311_reports: "pulled nearby 311 reports",
  get_eviction_filings: "checked eviction filings in the census tract",
  get_flood_zone: "checked the FEMA flood map",
  read_lease: "read the lease",
  check_security_deposit: "checked the security deposit against CT law",
  check_late_fee: "checked the late fee against CT law",
  check_upfront_fees: "checked an up-front fee against CT law",
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
}

// -------------------------------------------------------------- progress

function startProgress(hasLease) {
  $("#report").hidden = true;
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
      $("#progress-status").textContent = ev.message;
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
      renderReport(body.result, jobId);
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
const SEVERITY_WORD = { red: "Material concern", yellow: "Clarify before signing", green: "Satisfactory" };
const CATEGORY_WORD = { landlord: "Ownership", building: "Property", neighborhood: "Area", lease: "Lease" };
const ORDER = { red: 0, yellow: 1, green: 2 };

// "224 WOOSTER ST, NEW HAVEN, CT, 06511" -> "224 Wooster St, New Haven, CT 06511"
function prettyAddress(raw) {
  const parts = String(raw).split(",").map((p) => p.trim());
  const zip = /^\d{5}$/.test(parts[parts.length - 1]) ? parts.pop() : "";
  const state = parts.length > 2 ? parts.pop() : "";
  const words = parts.join(", ").toLowerCase().replace(/\b([a-z])/g, (c) => c.toUpperCase());
  return [words, [state, zip].filter(Boolean).join(" ")].filter(Boolean).join(", ");
}

function money(x) { return `$${Number(x).toFixed(4)}`; }
function n(x) { return Number(x || 0).toLocaleString(); }

function renderReport(result, jobId) {
  const r = result.report;
  const evidence = Object.fromEntries((result.evidence || []).map((e) => [e.id, e]));
  const findings = [...r.findings].sort((a, b) => ORDER[a.severity] - ORDER[b.severity]);

  const findingHtml = findings.map((f) => {
    const srcs = f.evidence_ids.map((id) => {
      const e = evidence[id];
      if (!e) return `<span>${esc(id)}</span>`;
      const label = id === "LEASE" ? "Your lease" : e.source;
      return e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener" title="${esc(id)}">${esc(label)}</a>`
                   : `<span title="${esc(id)}">${esc(label)}</span>`;
    }).join("");
    return `<article class="finding ${esc(f.severity)}">
      <div class="label">
        <span class="tag ${esc(f.severity)}">${SEVERITY_WORD[f.severity] || esc(f.severity)}</span><br>
        <span class="tag cat">${CATEGORY_WORD[f.category] || esc(f.category)}</span>
      </div>
      <div>
        <h3>${esc(f.title)}</h3>
        <p>${esc(f.detail)}</p>
        <div class="sources">Sources: ${srcs}</div>
      </div>
    </article>`;
  }).join("");

  const gaps = [...(r.gaps || [])];
  const safety = result.safety_events || [];
  const u = result.usage;
  const usageRows = u.by_agent.map((a) => `<tr><td>${esc(shown(a.agent))}</td><td>${n(a.requests)}</td><td>${n(a.input_tokens)}</td>
      <td>${n(a.cache_read_tokens)}</td><td>${n(a.output_tokens)}</td><td>${money(a.cost_usd)}</td></tr>`).join("");
  const t = u.totals;

  const when = new Date().toLocaleString("en-US", { dateStyle: "long", timeStyle: "short" });

  $("#report").innerHTML = `
    <section class="card">
      <div class="report-head">
        <div>
          <p class="eyebrow">Verification report</p>
          <div class="report-address">${esc(prettyAddress(result.address.matched_address))}</div>
        </div>
        <div class="report-meta">
          Report no. <b>${esc(String(jobId).toUpperCase())}</b><br>
          Issued <b>${esc(when)}</b><br>
          Lease reviewed: <b>${r.findings.some((f) => f.category === "lease") ? "Yes" : "No"}</b>
        </div>
      </div>
      <span class="verdict ${VERDICT_CLASS[r.verdict] || "caution"}">${esc(r.verdict)}</span>
      <p class="headline">${esc(r.headline)}</p>
      <p class="summary">${esc(r.summary)}</p>
    </section>

    <section class="card">
      <div class="card-head"><h2>Findings</h2><p>${findings.length} item${findings.length === 1 ? "" : "s"}, most significant first</p></div>
      ${findingHtml || '<p class="muted">No findings were significant enough to report.</p>'}
    </section>

    <section class="card">
      <div class="card-head"><h2>Questions for the landlord</h2><p>Ask before you sign</p></div>
      <ol class="questions">${r.questions_for_landlord.map((q) => `<li>${esc(q)}</li>`).join("")}</ol>
    </section>

    <section class="card">
      <div class="registry">
        <h3>Sex offender registries</h3>
        <p>We link to the official registries rather than searching them on your behalf. The official sites are the most current,
           and their records should be read with the context and cautions those sites provide.</p>
        <p><a href="${esc(result.registry_links.ct_registry)}" target="_blank" rel="noopener">Connecticut Sex Offender Registry</a>
           &nbsp;·&nbsp; <a href="${esc(result.registry_links.national_registry)}" target="_blank" rel="noopener">National Sex Offender Public Website (U.S. DOJ)</a></p>
      </div>
      ${gaps.length ? `<h3 style="margin:28px 0 8px;color:var(--navy)">Scope and limitations</h3><ul>${gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : ""}
    </section>

    <section class="card">
      <details class="behind">
        <summary>Methodology and cost</summary>
        <p class="small">Model: ${esc(u.model)}. All figures below are measured from this review.</p>
        <table>
          <thead><tr><th>Review</th><th>AI calls</th><th>Input tokens</th><th>Cached</th><th>Output tokens</th><th>Cost</th></tr></thead>
          <tbody>${usageRows}
            <tr class="total"><td>Total</td><td>${n(t.requests)}</td><td>${n(t.input_tokens)}</td><td>${n(t.cache_read_tokens)}</td>
            <td>${n(t.output_tokens)}</td><td>${money(t.cost_usd)}</td></tr></tbody>
        </table>
        <h3 style="color:var(--navy)">Safeguards applied</h3>
        ${safety.length ? `<ul class="safety-list">${safety.map((s) => `<li><b>${esc(shown(s.agent))}:</b> ${esc(s.detail)}</li>`).join("")}</ul>`
                        : '<p class="small">No safeguards were triggered during this review.</p>'}
      </details>
    </section>

    <div class="report-actions">
      <button class="secondary" onclick="window.print()">Print or save as PDF</button>
      <button class="primary" onclick="window.scrollTo({top:0,behavior:'smooth'})">New verification</button>
    </div>
  `;
  $("#report").hidden = false;
  $("#report").scrollIntoView({ behavior: "smooth", block: "start" });
}

loadSamples();
