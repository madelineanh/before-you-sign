const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

const TOOL_WORDS = {
  ask_landlord_investigator: "asked the Landlord Investigator to research the owner",
  ask_neighborhood_scout: "asked the Neighborhood Scout to check the surroundings",
  ask_lease_reviewer: "asked the Lease Reviewer to read the lease",
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
  $("#submit").textContent = "Check this apartment";
}

// -------------------------------------------------------------- progress

function startProgress(hasLease) {
  $("#report").hidden = true;
  $("#progress-card").hidden = false;
  $("#activity").innerHTML = "";
  $("#progress-status").textContent = "Getting started…";
  document.querySelectorAll("#team li").forEach((li) => {
    li.className = "";
    li.querySelector("em").textContent = "waiting";
  });
  if (!hasLease) {
    const li = document.querySelector('#team li[data-agent="Lease Reviewer"]');
    li.className = "skipped";
    li.querySelector("em").textContent = "no lease";
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
      setAgent(ev.agent, "working", "working…");
      break;
    case "agent_done":
      setAgent(ev.agent, "done", "done");
      break;
    case "agent_error":
      setAgent(ev.agent, "done", "could not finish");
      addActivity(`<b>${esc(ev.agent)}</b> could not finish.`, "safety");
      break;
    case "model_response":
      (ev.tool_calls || []).forEach((c) => {
        const words = TOOL_WORDS[c.tool] || `used ${esc(c.tool)}`;
        const arg = c.args && (c.args.company_name || c.args.owner_name);
        addActivity(`<b>${esc(ev.agent)}</b> ${words}${arg ? `: ${esc(arg)}` : ""}`);
      });
      break;
    case "safety":
      addActivity(`<b>Safety check:</b> ${esc(ev.detail)}`, "safety");
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
      $("#progress-status").textContent = `Finished in ${body.result.seconds} seconds.`;
      renderReport(body.result);
    } else {
      $("#progress-status").textContent = "The check stopped.";
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
const SEVERITY_WORD = { red: "Concern", yellow: "Ask about", green: "Reassuring" };
const ORDER = { red: 0, yellow: 1, green: 2 };

function money(x) { return `$${Number(x).toFixed(4)}`; }
function n(x) { return Number(x || 0).toLocaleString(); }

function renderReport(result) {
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
      <span class="tag ${esc(f.severity)}">${SEVERITY_WORD[f.severity] || esc(f.severity)}</span><span class="tag cat">${esc(f.category)}</span>
      <h3>${esc(f.title)}</h3>
      <p>${esc(f.detail)}</p>
      <div class="sources">Sources: ${srcs}</div>
    </article>`;
  }).join("");

  const gaps = [...(r.gaps || [])];
  const safety = result.safety_events || [];
  const u = result.usage;
  const usageRows = u.by_agent.map((a) => `<tr><td>${esc(a.agent)}</td><td>${n(a.requests)}</td><td>${n(a.input_tokens)}</td>
      <td>${n(a.cache_read_tokens)}</td><td>${n(a.output_tokens)}</td><td>${money(a.cost_usd)}</td></tr>`).join("");
  const t = u.totals;

  $("#report").innerHTML = `
    <section class="card verdict-card">
      <span class="verdict ${VERDICT_CLASS[r.verdict] || "caution"}">${esc(r.verdict)}</span>
      <p class="headline">${esc(r.headline)}</p>
      <p class="matched">${esc(result.address.matched_address)}</p>
      <p class="summary">${esc(r.summary)}</p>
    </section>

    <section class="card">
      <h2>What we found</h2>
      ${findingHtml || '<p class="muted">No findings were strong enough to report.</p>'}
    </section>

    <section class="card">
      <h2>Questions to ask the landlord</h2>
      <ol class="questions">${r.questions_for_landlord.map((q) => `<li>${esc(q)}</li>`).join("")}</ol>
    </section>

    <section class="card">
      <div class="registry">
        <h3>Check the official sex-offender registries</h3>
        <p>We link to the official registries instead of searching them for you: the official sites are the most current,
           and their records should be read with the context and warnings those sites provide.</p>
        <p><a href="${esc(result.registry_links.ct_registry)}" target="_blank" rel="noopener">Connecticut Sex Offender Registry</a>
           &nbsp;·&nbsp; <a href="${esc(result.registry_links.national_registry)}" target="_blank" rel="noopener">National Sex Offender Public Website (U.S. DOJ)</a></p>
      </div>
      ${gaps.length ? `<h3 style="margin-top:22px">What we couldn't check</h3><ul>${gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : ""}
    </section>

    <section class="card">
      <details class="behind">
        <summary>Behind the scenes</summary>
        <p class="small">Model: ${esc(u.model)}. Every number below is measured from this run.</p>
        <table>
          <thead><tr><th>Agent</th><th>AI calls</th><th>Input tokens</th><th>Cached</th><th>Output tokens</th><th>Cost</th></tr></thead>
          <tbody>${usageRows}
            <tr class="total"><td>Total</td><td>${n(t.requests)}</td><td>${n(t.input_tokens)}</td><td>${n(t.cache_read_tokens)}</td>
            <td>${n(t.output_tokens)}</td><td>${money(t.cost_usd)}</td></tr></tbody>
        </table>
        <h3>Safety checks during this run</h3>
        ${safety.length ? `<ul class="safety-list">${safety.map((s) => `<li><b>${esc(s.agent)}:</b> ${esc(s.detail)}</li>`).join("")}</ul>`
                        : '<p class="small">No safety checks were triggered.</p>'}
      </details>
    </section>

    <div class="again"><button class="primary" onclick="window.scrollTo({top:0,behavior:'smooth'})">Check another apartment</button></div>
  `;
  $("#report").hidden = false;
  $("#report").scrollIntoView({ behavior: "smooth", block: "start" });
}

loadSamples();
