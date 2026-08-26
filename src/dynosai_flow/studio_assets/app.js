const $ = (id) => document.getElementById(id);
let overview = null;

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
  return data;
}

function showAlert(message) { const node=$("alert"); node.textContent=message; node.classList.remove("hidden"); setTimeout(()=>node.classList.add("hidden"),6000); }
function esc(value){return String(value ?? "").replace(/[&<>"']/g,(ch)=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));}
function list(items, mapper, empty="Nothing to show yet.") { return `<div class="list">${items.length ? items.map(mapper).join("") : `<div class="list-item"><small>${esc(empty)}</small></div>`}</div>`; }

async function refresh() {
  try {
    const health = await api("/api/health");
    $("connection").textContent="Local server"; $("connection").className="pill ok";
    $("version").textContent=`v${health.version} · local only`;
    $("project-path").textContent=health.root;
    $("health").textContent=JSON.stringify(health,null,2);
    overview = await api("/api/overview");
    render(overview);
  } catch (error) { $("connection").textContent="Disconnected"; $("connection").className="pill warn"; showAlert(error.message); }
}

function render(data) {
  const detection=data.detection || {};
  $("project-name").textContent=data.project || "DynosAI Studio";
  $("project-classification").textContent=(detection.classification || "Unknown project").replaceAll("_"," ");
  $("initialize").classList.toggle("hidden", !!data.initialized || detection.classification === "DIRTY_GIT_REPO");
  $("initialize").textContent=detection.classification === "NEW_CODE_NO_GIT" ? "Initialize Git + DynosAI" : "Initialize DynosAI";
  const risk=data.risk || {level:"low",score:0,signals:[],recommendations:[]};
  $("risk-level").textContent=(risk.level || "low").toUpperCase(); $("risk-level").className=`risk-${risk.level}`; $("risk-score").textContent=`${risk.score}/100 advisory`;
  const work=data.work || []; $("active-work").textContent=work.filter(x=>x.state!=="done").length;
  const approved=data.validations?.approved || {}; $("validation-count").textContent=Object.values(approved).filter(x=>x.approved).length;
  const blockers=data.blockers || {items:[]}; $("blocker-count").textContent=blockers.items.length; $("blocker-summary").textContent=blockers.blocked?"needs attention":"ready";
  $("blockers").innerHTML=list(blockers.items || [], item=>`<div class="list-item"><strong>${esc(item.code)}</strong><small>${esc(item.message)}</small><br><small>Next: ${esc(item.action)}</small></div>`, blockers.recommended_action || "No blockers. Start or continue governed work.");
  const candidates=data.validations?.candidates || [];
  $("validations").innerHTML=list(candidates, item=>{const current=approved[item.name];return `<div class="list-item"><strong>${esc(item.name)} ${current?.approved?"✓":""}</strong><small>${esc(item.command.join(" "))}</small><br><small>${esc(item.reason)}</small></div>`}, "No common validation configuration was discovered.");
  $("approve-validations").classList.toggle("hidden", !data.initialized || candidates.length===0);
  $("work-list").innerHTML=list(work,item=>`<div class="list-item"><strong>${esc(item.id)} · ${esc(item.title)}</strong><small>${esc(item.description)}</small><br><span class="state-chip">${esc(item.state)}</span></div>`,"No governed work items yet.");
  $("risk-signals").innerHTML=list(risk.signals || [],item=>`<div class="list-item"><strong>+${esc(item.weight)} · ${esc(item.code)}</strong><small>${esc(item.message)}</small></div>`,"No elevated deterministic risk signals.");
  $("risk-recommendations").innerHTML=list(risk.recommendations || [],item=>`<div class="list-item"><small>${esc(item)}</small></div>`);
  $("risk-title").textContent=`${(risk.level||"low").toUpperCase()} · ${risk.score}/100`;
  $("raw-overview").textContent=JSON.stringify(data,null,2);
  loadEvents();
}

async function loadEvents(){
  if(!overview?.initialized){$("events").textContent="Initialize DynosAI to create the durable event stream.";return;}
  try{const data=await api("/api/events");$("events").textContent=JSON.stringify(data.items.slice(-25),null,2);}catch(error){$("events").textContent=error.message;}
}

document.querySelectorAll(".nav-item").forEach(button=>button.addEventListener("click",()=>{
  document.querySelectorAll(".nav-item").forEach(x=>x.classList.toggle("active",x===button));
  document.querySelectorAll(".view").forEach(x=>x.classList.toggle("active",x.id===button.dataset.view));
}));
$("advanced-toggle").addEventListener("change",event=>document.body.classList.toggle("advanced",event.target.checked));
$("refresh").addEventListener("click",refresh);
$("initialize").addEventListener("click",async()=>{try{const allow_git_init=overview?.detection?.classification === "NEW_CODE_NO_GIT";await api("/api/project/initialize",{method:"POST",body:JSON.stringify({agent:"codex",allow_git_init})});await refresh();}catch(error){showAlert(error.message);}});
$("approve-validations").addEventListener("click",async()=>{try{await api("/api/validations/approve",{method:"POST",body:JSON.stringify({})});await refresh();}catch(error){showAlert(error.message);}});
$("start-work").addEventListener("click",async()=>{try{const description=$("work-description").value.trim();if(!description)throw new Error("Describe the change first.");await api("/api/work/start",{method:"POST",body:JSON.stringify({description,provider:$("provider").value,workspace_strategy:$("workspace").value})});$("work-description").value="";await refresh();}catch(error){showAlert(error.message);}});
refresh();
