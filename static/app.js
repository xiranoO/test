const $ = (id) => document.getElementById(id);
let currentRun = null;
let approvalToken = null;
let expectedPhrase = "";

function setStatus(message, error = false) {
  const box = $("status-box");
  box.hidden = false;
  box.textContent = message;
  box.className = `status-box${error ? " error" : ""}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "请求失败，请稍后再试。");
  return data;
}

function addItems(target, items, formatter, emptyText) {
  target.replaceChildren();
  if (!items.length) {
    const node = document.createElement("div"); node.className = "item"; node.textContent = emptyText; target.append(node); return;
  }
  items.forEach((item) => {
    const node = document.createElement("div"); node.className = "item"; formatter(node, item); target.append(node);
  });
}

function renderRun(run) {
  currentRun = run.id;
  const a = run.analysis;
  $("empty-state").hidden = true; $("result-content").hidden = false; $("review-panel").hidden = false;
  $("issue-title").textContent = `#${a.issue.number} · ${a.issue.title}`;
  $("provider-badge").textContent = a.analysis_provider;
  $("issue-type").textContent = a.classification.issue_type;
  $("priority").textContent = a.classification.priority;
  $("component").textContent = a.classification.component;
  $("comment-count").textContent = `${run.source.comments_loaded || 0} 条`;
  $("warning").hidden = !a.provider_warning; $("warning").textContent = a.provider_warning ? `模型已安全回退：${a.provider_warning}` : "";
  addItems($("files-list"), a.related_files, (n, x) => { const b=document.createElement("b"); b.textContent=x.path; const s=document.createElement("small"); s.textContent=x.reason; n.append(b,s); }, "未定位到高相关文件");
  addItems($("duplicates-list"), a.duplicates, (n, x) => { n.textContent=`#${x.issue_number} · ${x.title}（${Math.round(x.similarity*100)}%）`; }, "未发现高置信度重复 Issue");
  const reproduction = [...a.reproduction.confirmed_facts.map(x=>`已确认：${x}`), ...a.reproduction.inferred_steps.map(x=>`待验证：${x}`)];
  addItems($("reproduction-list"), reproduction, (n,x)=>{n.textContent=x;}, "暂无复现信息");
  addItems($("fix-list"), [a.fix_plan.likely_cause, ...a.fix_plan.changes], (n,x)=>{n.textContent=x;}, "暂无修复建议");
  const comments = a.issue.comments || []; $("comment-summary").textContent = `(${comments.length})`;
  addItems($("comments-list"), comments, (n,x)=>{ n.className="comment"; const h=document.createElement("header"); const author=document.createElement("b"); author.textContent=`@${x.author}`; if(["OWNER","MEMBER","COLLABORATOR"].includes(x.author_association)) author.className="member"; const role=document.createElement("span"); role.textContent=x.author_association; h.append(author,role); const p=document.createElement("pre"); p.textContent=x.body; n.append(h,p); }, "没有评论证据");
  $("draft").value = run.draft; updateDraftCount(); $("run-status").textContent = "等待审批";
  $("prepare-button").disabled = ["simulated_submitted", "github_submitted"].includes(run.status);
  renderAudit([{action:"analysis_created", created_at:run.created_at}]);
}

function renderAudit(events) {
  const names={analysis_created:"分析已生成",approval_requested:"等待二次确认",simulation_submitted:"模拟提交完成",github_comment_submitted:"GitHub 评论已发布"};
  $("audit-list").replaceChildren(...events.map(e=>{const li=document.createElement("li"); li.textContent=`${names[e.action]||e.action} · ${new Date(e.created_at).toLocaleString()}`; return li;}));
}

function updateDraftCount(){ $("draft-count").textContent = `${$("draft").value.length} 字符`; }
$("draft").addEventListener("input", updateDraftCount);

api("/health").then((health) => {
  if (health.github_write_enabled) {
    $("safety-label").textContent = "GitHub 受控写入";
    $("write-state").textContent = "需三重确认";
    $("github-mode-option").disabled = false;
    $("github-mode-option").textContent = "真实发布到 GitHub";
  }
}).catch(() => {});

$("submission-mode").addEventListener("change", () => {
  const real = $("submission-mode").value === "github";
  $("phrase-field").hidden = !real;
  $("confirmation-copy").textContent = real
    ? "这会把当前草稿真实发布到指定 GitHub Issue。请逐字核对仓库、Issue 和回复内容。"
    : "本次操作只生成模拟回执，不会向 GitHub 发布评论或修改标签。";
  $("confirm-button").textContent = real ? "确认真实发布" : "确认模拟提交";
});

function selectedIssues() {
  return [...document.querySelectorAll(".issue-select:checked")];
}

function updateSelectionCount() {
  const count = selectedIssues().length;
  const all = [...document.querySelectorAll(".issue-select")];
  const master = $("select-all-issues");
  master.checked = all.length > 0 && count === Math.min(all.length, 10);
  master.indeterminate = count > 0 && !master.checked;
  $("selection-count").textContent = `已选择 ${count} / 10`;
  $("batch-analyze").disabled = count === 0;
}

function queueStatusText(status) {
  const names = {waiting_for_approval:"待审批",waiting_for_confirmation:"待确认",simulated_submitted:"已模拟",github_submitted:"已发布"};
  return names[status] || "待分析";
}

function renderIssueQueue(issues, histories) {
  issues = [...issues].sort((left, right) => left.number - right.number);
  const latest = new Map();
  histories.forEach((run) => { if (!latest.has(run.issue_number)) latest.set(run.issue_number, run); });
  $("issue-queue").replaceChildren(...issues.map((issue) => {
    const row = document.createElement("div"); row.className = "queue-item"; row.dataset.issueNumber = issue.number;
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.className = "issue-select"; checkbox.setAttribute("aria-label", `选择 Issue #${issue.number}`);
    checkbox.addEventListener("change", () => { if (selectedIssues().length > 10) { checkbox.checked=false; setStatus("单次最多选择 10 条 Issue。", true); } updateSelectionCount(); });
    const copy = document.createElement("div"); copy.className="queue-copy"; const title=document.createElement("b"); title.textContent=`#${issue.number} · ${issue.title}`; const labels=document.createElement("small"); labels.textContent=issue.labels.length?issue.labels.join(" · "):"无标签"; copy.append(title,labels);
    const status=document.createElement("button"); status.type="button"; status.className="queue-status"; const history=latest.get(issue.number); status.textContent=history?queueStatusText(history.status):"待分析";
    if(history){row.dataset.runId=history.id;status.classList.add("done");status.addEventListener("click",()=>openQueueRun(row));} else {status.disabled=true;}
    row.append(checkbox,copy,status); return row;
  }));
  updateSelectionCount();
}

$("select-all-issues").addEventListener("change", (event) => {
  const checkboxes = [...document.querySelectorAll(".issue-select")];
  checkboxes.forEach((checkbox, index) => { checkbox.checked = event.target.checked && index < 10; });
  updateSelectionCount();
  if (event.target.checked && checkboxes.length > 10) setStatus(`当前有 ${checkboxes.length} 条 Issue，已按显示顺序选择前 10 条。`);
});

$("clear-selection").addEventListener("click", () => {
  document.querySelectorAll(".issue-select").forEach((checkbox) => { checkbox.checked = false; });
  updateSelectionCount();
});

async function openQueueRun(row) {
  if (!row.dataset.runId) return;
  try { const run=await api(`/api/runs/${row.dataset.runId}`); renderRun(run); renderAudit(run.events||[]); window.scrollTo({top:$("result-content").offsetTop+300,behavior:"smooth"}); }
  catch(error){setStatus(error.message,true);}
}

$("load-issues").addEventListener("click", async () => {
  const repository=$("repository").value.trim(); if(!repository){setStatus("请先填写 owner/repo 格式的仓库名。",true);return;}
  const button=$("load-issues");button.disabled=true;setStatus("正在读取仓库的 Open Issues……");
  try {
    const [listing,history]=await Promise.all([
      api("/api/repository-issues",{method:"POST",body:JSON.stringify({repository,limit:50})}),
      api(`/api/runs?repository=${encodeURIComponent(repository)}&limit=100`)
    ]);
    $("batch-panel").hidden=false;$("batch-repository").textContent=`${listing.repository} · ${listing.issues.length} 条开放 Issue`;
    renderIssueQueue(listing.issues,history.runs);setStatus(`已载入 ${listing.issues.length} 条 Open Issues，可选择最多 10 条。`);
  } catch(error){setStatus(error.message,true);} finally{button.disabled=false;}
});

$("batch-analyze").addEventListener("click", async () => {
  const selected=selectedIssues();if(!selected.length)return;const button=$("batch-analyze");button.disabled=true;
  let completed=0;let failed=0;
  for(const checkbox of selected){
    const row=checkbox.closest(".queue-item");const status=row.querySelector(".queue-status");status.disabled=true;status.className="queue-status working";status.textContent="分析中…";
    try {
      const run=await api("/api/analyze",{method:"POST",body:JSON.stringify({repository:$("repository").value.trim(),issue_number:Number(row.dataset.issueNumber),provider:$("provider").value})});
      row.dataset.runId=run.id;status.disabled=false;status.className="queue-status done";status.textContent="查看结果";status.onclick=()=>openQueueRun(row);completed+=1;renderRun(run);
    } catch(error){status.className="queue-status failed";status.textContent="分析失败";status.title=error.message;failed+=1;}
  }
  selected.forEach(x=>{x.checked=false;});updateSelectionCount();setStatus(`队列完成：成功 ${completed} 条，失败 ${failed} 条。${failed?" 将鼠标移到失败状态可查看原因。":""}`,failed>0);
});

$("analyze-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button=event.submitter; button.disabled=true; setStatus("正在读取证据并分析，首次克隆仓库可能需要一些时间……");
  try {
    const run=await api("/api/analyze",{method:"POST",body:JSON.stringify({repository:$("repository").value.trim()||null,issue_number:Number($("issue-number").value),provider:$("provider").value})});
    renderRun(run); setStatus(`分析完成 · ${run.source.mode} · ${run.source.comments_loaded || 0} 条评论证据`);
  } catch(error) { setStatus(error.message,true); } finally { button.disabled=false; }
});

$("prepare-button").addEventListener("click", async () => {
  if(!currentRun)return; const button=$("prepare-button"); button.disabled=true;
  try { const data=await api(`/api/runs/${currentRun}/prepare-approval`,{method:"POST",body:JSON.stringify({draft:$("draft").value})}); approvalToken=data.approval_token; expectedPhrase=data.real_confirmation_phrase||""; $("approval-token").textContent=approvalToken; $("expected-phrase").textContent=expectedPhrase ? `必须准确输入：${expectedPhrase}` : "当前记录不能发布到 GitHub"; $("confirmation-phrase").value=""; $("submission-mode").value="simulation"; $("phrase-field").hidden=true; $("confirmation-copy").textContent="本次操作只生成模拟回执，不会向 GitHub 发布评论或修改标签。"; $("confirm-button").textContent="确认模拟提交"; $("run-status").textContent="等待二次确认"; $("confirm-dialog").showModal(); }
  catch(error){setStatus(error.message,true);} finally{button.disabled=false;}
});

$("confirm-button").addEventListener("click", async (event) => {
  event.preventDefault();
  try { const mode=$("submission-mode").value; const receipt=await api(`/api/runs/${currentRun}/confirm`,{method:"POST",body:JSON.stringify({approval_token:approvalToken,mode,confirmation_phrase:$("confirmation-phrase").value})}); $("confirm-dialog").close(); $("run-status").textContent=mode==="github"?"GitHub 发布完成":"模拟提交完成"; $("prepare-button").disabled=true; setStatus(mode==="github"?`评论已发布：${receipt.comment_url}`:"模拟提交已完成；GitHub 未发生任何写入。"); const run=await api(`/api/runs/${currentRun}`); renderAudit(run.events); }
  catch(error){$("confirm-dialog").close();setStatus(error.message,true);}
});
