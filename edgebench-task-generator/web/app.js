(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const params = new URLSearchParams(location.search);
  const token = params.get('token') || localStorage.getItem('edgebench-console-token');
  if (token) localStorage.setItem('edgebench-console-token', token);
  const LABELS = {
    'needs-owner-decision':'待负责人决定', 'replacement-required':'待替换',
    'repair-required':'待修复', 'dispatch-created':'已准备派发', 'rollout-running':'试跑中',
    'rollout-pending':'待试跑批准', 'rollout-authorized':'试跑已批准',
    'awaiting-approval':'等待审批', 'hardening-r1-required':'待第一次加难',
    'hardening-r2-required':'待第二次加难', 'queued':'排队中', 'active':'已登记',
    'running':'运行中', 'idle':'空闲', 'fired':'已封存', 'complete':'已完成',
    'waiting-qc':'待质检', 'unbound':'未绑定', 'unsupported':'未适配', 'unavailable':'不可用',
    'binding-mismatch':'绑定不匹配', 'unknown':'未知', 'ok':'正常', 'partial':'部分加载',
    'pending':'待回答', 'answered':'已回答', 'cancelled':'已取消',
    'healthy':'心跳正常', 'heartbeat-missed':'心跳超时', 'stalled':'疑似停工',
    'waiting':'等待阶段',
    'standby':'负责人批准待命', 'held-owner':'等待负责人，通道已关闭',
    'succeeded':'已完成', 'failed':'失败', 'interrupted':'已中断',
    'pass':'通过', 'fail':'不通过', 'repair_required':'待修复', 'pass_with_exception':'有例外待审批',
    'heartbeat':'心跳', 'completed':'阶段完成', 'blocked':'阻塞', 'question':'提问',
    'CONTINUE':'继续观测', 'ACCEPT':'增长检查通过', 'EARLY_TOO_EASY':'早期过浅',
    'INFRA_FAILURE':'基础设施故障', 'EVIDENCE_INCOMPLETE':'证据不完整',
    'FINAL_POLICY_REVIEW':'最终结果待审核', 'FINAL_POLICY_FAIL':'最终结果不符合要求',
    'REQUIREMENTS_LOCKED':'需求已锁定', 'CAMPAIGN_READY':'批次已初始化', 'not-locked':'尚未锁定',
    'seed-discovery':'发现种子', 'seed-review':'种子待审', 'authoring':'出题与加难',
    'qc-review':'质检阶段', 'assigned':'已分配',
    'producer':'出题负责人', 'qc':'独立质检员', 'rollout':'试跑观察员',
    'seed-worker':'种子子任务', 'prototype-worker':'原型子任务', 'edgebench-producer':'出题负责人',
    'edgebench-qc':'质检员', 'edgebench-supervisor':'上司', 'edgebench-scout':'来源搜索员',
    'edgebench-seed-worker':'种子子任务', 'edgebench-seed-verifier':'种子核验员',
    'edgebench-prototype-worker':'原型子任务', 'edgebench-rollout':'试跑观察员', 'build':'主会话',
    'owner':'你', 'supervisor':'上司', 'employee':'员工', 'watchdog':'监控告警', 'user':'你', 'assistant':'智能体',
    'opencode-task':'OpenCode 会话', 'orca-cli':'Orca 编排',
    'employee-state':'员工事项', 'rollout-gate':'试跑检查',
    'incomplete':'证据未完成', 'not-classified':'尚未区分失败与未验证',
    'verified':'报告字节已核验', 'changed':'报告已变更',
  };
  const EVENTS = {
    supervisor_initialized:'上司已登记', employee_hired:'员工已登记', employee_dispatched:'已生成派发工作单',
    employee_session_bound:'已绑定实际会话', employee_reported:'员工提交报告', employee_fired:'员工已封存',
    child_requested:'员工申请子任务', hardening_challenge_recorded:'上司提出加难要求',
    producer_seed_bound:'种子已审核绑定', employee_dedup_claimed:'种子指纹已占用',
    employee_task_registered:'任务哈希已登记', qc_decision_recorded:'质检结果已记录',
    qc_resolution_recorded:'质检决定已记录', owner_decision_recorded:'负责人决定已记录',
    rollout_gate_recorded:'试跑检查已记录', replacement_order_created:'补员工作单已生成',
    message_queued:'消息已入队', owner_command_queued:'负责人指令已入队',
    employee_session_message_sent:'消息已提交员工会话', opencode_form_answered:'已回答 OpenCode 原生问题',
    supervisor_session_message_sent:'消息已提交真实上司', native_question_answered:'回答已交给等待中的工具',
    qc_owner_review_required:'QC 返修后仍未通过，等待负责人复核',
    supervisor_message_delivery_failed:'上司接收未确认',
  };
  const label = value => LABELS[value] || value || '未报告';
  const ago = value => {
    if (!value) return '—';
    const stamp = typeof value === 'number' ? value : Date.parse(value);
    if (!Number.isFinite(stamp)) return '时间未知';
    const seconds = Math.max(0, Math.round((Date.now() - stamp) / 1000));
    return seconds < 60 ? `${seconds} 秒前` : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟前` : `${Math.floor(seconds / 3600)} 小时前`;
  };
  async function api(path, options = {}) {
    const response = await fetch(path, {...options, headers: {
      ...(token ? {Authorization:`Bearer ${token}`} : {}), 'Content-Type':'application/json', ...(options.headers || {}),
    }});
    const value = await response.json().catch(() => ({error:'服务器未返回有效数据，请刷新重试。'}));
    if (!response.ok) {
      const error = new Error(value.error || `操作失败（HTTP ${response.status}）`);
      error.status = response.status; throw error;
    }
    return value;
  }

  let supervisors = [];
  let scope = params.get('supervisor_id');
  let data = null;
  let events = [];
  let expanded = false;
  let view = 'cards';
  let filter = 'all';
  let selection = null;
  let selectionEpoch = 0;
  let stream = null;
  let updating = false;
  const questionRequests = new Map();
  let globalForms = [];
  let detailForms = [];
  let nativeForms = [];
  let chatSending = false;
  let supervisorChatLoading = false;

  const query = fields => new URLSearchParams({supervisor_id:scope, ...fields});
  function setConnection(text, bad = false) {
    $('connection').textContent = text;
    $('connection').className = `connection${bad ? ' error' : ''}`;
  }
  function renderWorkspaces() {
    $('supervisor-list').innerHTML = supervisors.map(item => `<button type="button" class="workspace-item ${item.supervisor_id === scope ? 'active' : ''}" data-id="${esc(item.supervisor_id)}" title="${esc(item.campaign)}"><span class="workspace-icon">${esc(item.benchmark?.slice(0, 1) || 'B')}</span><div><strong>${esc(item.workspace?.campaign_id || item.benchmark)}</strong><small>目标 ${esc(item.workspace?.target_task_count ?? '—')} 题 · ${esc(label(item.workspace?.lock_state))}</small></div></button>`).join('');
    $('supervisor-list').querySelectorAll('button').forEach(b => b.addEventListener('click', () => switchWorkspace(b.dataset.id)));
  }
  function stageMarkup(e) {
    const r = e.hardening_revision;
    return `<div class="task-stages">${[['种子',Boolean(e.seed_id)],['初版',Number.isInteger(r) && r >= 0],['加难一',Number.isInteger(r) && r >= 1],['加难二',Number.isInteger(r) && r >= 2],['质检',e.qc?.decision === 'pass'],['试跑',Boolean(e.rollout_gate)]].map(([text,ready]) => `<span class="task-stage ${ready ? 'recorded' : ''}">${text}</span>`).join('')}</div>`;
  }
  function action(e) {
    if (e.runtime?.status === 'unbound') return '工作单已准备，实际会话尚未绑定。';
    if (e.runtime?.status === 'binding-mismatch') return '实际会话与员工工作目录不匹配。';
    return e.report?.summary || (e.rollout_gate ? label(e.rollout_gate.status) : e.qc ? `质检：${label(e.qc.decision)}` : label(e.status));
  }
  function renderDashboard(value) {
    if (value.supervisor_id !== scope) return;
    data = value;
    const employees = value.employees || [];
    const w = value.workforce || {};
    const info = value.workspace || {};
    $('active').textContent = w.runtime_running ?? '—';
    $('active-note').textContent = `登记 ${employees.length} 人 · 未绑定 ${w.unbound ?? '—'} 人`;
    $('capacity').textContent = `${value.capacity?.available ?? '—'} / ${value.capacity?.max_concurrent ?? '—'}`;
    $('alert-count').textContent = value.alerts?.length ?? 0;
    const ownerReviews = (value.alerts || []).filter(a => a.owner_review).length;
    $('alert-note').textContent = ownerReviews ? `${ownerReviews} 项 QC 需要你复核，其余待上司处理` : value.alerts?.length ? '等待上司审查，不等于负责人审批' : '暂无告警';
    const supervisor = value.supervisor_runtime || {status:'unbound'};
    $('health').textContent = supervisor.status === 'running' ? '上司运行中' : `上司${label(supervisor.status)}`;
    $('supervisor-status').textContent = label(supervisor.status);
    $('supervisor-identity').textContent = supervisor.session_id
      ? `${supervisor.title || '已绑定上司'} · ${supervisor.session_id} · 实际 Agent：${label(supervisor.agent)}`
      : '本批次没有绑定真实上司会话。';
    $('supervisor-note').textContent = `${supervisor.reason || ''} 最近实际活动：${ago(supervisor.updated_at)}。查看不会唤醒；发送消息才会恢复该会话。`;
    const secretary = value.secretary || {};
    const secretaryStatus = $('secretary-status');
    const secretaryNote = $('secretary-note');
    if (secretaryStatus) secretaryStatus.textContent = `${label(secretary.status)} · 待处理 ${secretary.pending_count ?? 0}`;
    if (secretaryNote) secretaryNote.textContent = `秘书每 ${secretary.interval_seconds ?? 180} 秒合并一次下属报告；已吸收 ${secretary.consumed_count ?? 0} 条，紧急待处理 ${secretary.pending_urgent_count ?? 0} 条。`;
    const runner=value.runtime_capabilities || {};
    $('runner-capability').textContent = runner.prompt_api
      ? `启动能力：可恢复既有绑定 session（prompt/resume）；${runner.process_launch ? 'CLI runner 可用。' : '进程 runner 不可用。'} MCP 资源列表不等于 runner。`
      : '启动能力：不可用；沟通开启不代表可以启动员工。';
    const gate=value.communication_control || {worker_channel_open:true};
    if(gate.permissions_guarded) $('health').textContent='负责人接管（只读）';
    $('owner-control-status').textContent=gate.worker_channel_open ? '上下级自动沟通通道：开启' : '上下级自动沟通通道：已关闭；负责人消息和原生回答仍可用';
    $('owner-control-reason').textContent=`${gate.reason || '上司需要你决定时，自动关闭员工/告警投递和员工派发。'} ${gate.permissions_guarded ? '同一上司会话已启用原生只读权限，不能发命令或派子任务。' : ''} 恢复不会回放历史告警，也不批准任何候选。`;
    $('owner-pause').disabled=Boolean(gate.owner_paused);
    $('owner-resume').disabled=!gate.owner_paused;
    const actions={close_candidate:'关闭候选',shelve_seed:'搁置种子',replace_seed:'更换种子',fire:'淘汰员工',standby:'转为待命'};
    $('owner-request-list').innerHTML=(value.owner_requests || []).map(r=>`<article class="owner-request"><h3>${esc(r.employee_id)} · 待你决定：${esc(actions[r.action] || r.action)}</h3><p>${esc(r.reason)}</p><p class="form-hint">证据：${esc(r.report_path)}<br>SHA-256：${esc(r.report_sha256)}<br>提案：${esc(r.request_id)}</p><p class="form-hint">本提案尚未执行。上司应提交真实原生 question 等待你的选择。</p></article>`).join('');
    const boundWorker=selection?.sessionId && employees.some(e=>e.session_id===selection.sessionId || (e.children || []).some(c=>c.session_id===selection.sessionId));
    if(boundWorker && !gate.worker_channel_open) {
      $('employee-chat-input').disabled=true; $('employee-chat-send').disabled=true;
      $('employee-chat-status').textContent='负责人已关闭上下级通道；员工消息暂不投递，可直接联系上司。';
    }
    $('supervisor-inspect').disabled = !supervisor.can_chat;
    document.querySelector('.supervisor-panel').classList.toggle('warning', supervisor.status !== 'running');
    $('command-input').disabled = !supervisor.can_chat;
    $('command-send').disabled = !supervisor.can_chat;
    $('updated').textContent = new Date().toLocaleTimeString('zh-CN');
    $('workspace-title').textContent = info.campaign_id || scope;
    $('workspace-heading').textContent = `${value.benchmark || '本工作空间'}生产现场`;
    $('workspace-subtitle').textContent = `目标 ${info.target_task_count ?? '—'} 题 · 配置 ${info.profile_id || '未锁定'} · 交付 ${info.delivery_format || '待定'} · ${label(info.lock_state)}`;
    $('side-status').textContent = `本工作空间 ${employees.length} 名员工`;
    $('workforce-summary').textContent = `登记 ${employees.length} 名员工，核验运行 ${w.runtime_running ?? '未知'} 名，未绑定 ${w.unbound ?? '未知'} 名。点击员工查看其实际会话与验收阶段。`;
    $('workforce-summary').classList.toggle('warning', Boolean(w.unbound));
    renderEmployees(); renderSessions(value.opencode_sessions || {}); renderAlerts();
  }
  function renderEmployees() {
    const all = data?.employees || [];
    const list = filter === 'attention' ? all.filter(e => ['needs-owner-decision','repair-required','replacement-required','awaiting-approval','rollout-pending'].includes(e.status)) : filter === 'active' ? all.filter(e => !['fired','complete'].includes(e.status)) : all;
    $('agent-grid').classList.toggle('timeline-mode', view === 'timeline');
    $('agent-grid').innerHTML = list.map(e => {
      const bad = e.status === 'fired' || e.status === 'failed';
      const warn = ['needs-owner-decision','replacement-required','repair-required'].includes(e.status);
      const healthWarn = e.health?.attention;
      return `<article class="agent-card" style="--card-tint:${bad ? '#fbe7e5' : healthWarn || warn ? '#fff0d9' : '#e3f3e9'}"><div class="card-top"><div class="agent-identity"><span class="agent-avatar">${esc(label(e.role).slice(0,1))}</span><div><strong title="${esc(e.employee_id)}">${esc(e.employee_id)}</strong><small>${esc(label(e.role))} · ${esc(label(e.transport))}</small></div></div><span title="${esc(e.status)}" class="card-status ${bad ? 'bad' : healthWarn || warn ? 'warn' : ''}"><i></i>${esc(label(e.status))}</span></div><div class="card-body"><div class="card-action">当前 <b>${esc(action(e))}</b></div><small>阶段：${esc(label(e.phase))} · 会话：${esc(label(e.runtime?.status))} · ${esc(label(e.health?.status))}</small>${e.health?.attention ? `<p class="health-warning">${esc(e.health.reason)} · 最后报告 ${esc(ago(e.health.last_report_at))}</p>` : ''}${stageMarkup(e)}</div><div class="card-footer"><span title="${esc(e.session_id || '')}">${esc(e.session_id || e.seed_id || '未绑定会话')}</span><button type="button" data-employee="${esc(e.employee_id)}">查看详情 →</button></div></article>`;
    }).join('') || '<div class="empty">当前筛选没有员工；临时 OpenCode 会话不会自动成为出题员工。</div>';
    $('agent-grid').querySelectorAll('[data-employee]').forEach(b => b.addEventListener('click', () => showEmployee(b.dataset.employee)));
  }
   function renderSessions(snapshot) {
    const allowed = Array.isArray(data?.visible_session_ids) ? new Set(data.visible_session_ids) : null;
    const sessions = (snapshot.sessions || []).filter(s => !allowed || allowed.has(s.session_id));
    $('session-summary').textContent = snapshot.status === 'unavailable' ? 'OpenCode 运行时不可用' : `管理范围 · 运行 ${sessions.filter(s => s.status === 'running').length} 个 · 共 ${sessions.length} 个`;
    $('session-grid').innerHTML = sessions.map(s => `<button type="button" class="session-card ${s.status === 'running' ? 'running' : ''}" data-session="${esc(s.session_id)}" title="查看此会话，不会发送消息"><i class="session-dot"></i><div><strong>${esc(s.title)}</strong><small>${esc(label(s.agent))} · ${esc(s.session_id)}${s.parent_id ? ` · 父会话 ${esc(s.parent_id)}` : ''}</small></div><span class="session-badge">${esc(label(s.status))}</span></button>`).join('') || `<div class="empty">${snapshot.status === 'unavailable' ? '无法查询真实会话，请检查 OpenCode 服务。' : '当前扫描范围没有会话。'}</div>`;
    $('session-grid').querySelectorAll('[data-session]').forEach(b => b.addEventListener('click', () => showSession(b.dataset.session)));
  }
  function renderAlerts() {
    const alerts = data?.alerts || [];
    const seen = new Set();
    $('alerts').innerHTML = alerts.filter(a => {const key=`${a.employee_id}:${a.status}:${a.kind}`; if(seen.has(key)) return false; seen.add(key); return true;}).map(a => `<div class="attention-item"><strong>${esc(a.employee_id)} · ${a.owner_review ? `QC-${esc(a.owner_review.round)} 仍未通过，等你复核` : a.kind === 'employee-watch' ? `员工${esc(label(a.status))}` : esc(label(a.status))}</strong><small>${esc(a.owner_review?.summary || (a.kind === 'employee-watch' ? a.health?.reason : null) || a.report?.summary || data.employees?.find(e => e.employee_id === a.employee_id)?.report?.summary || '阶段报告待上司处理；没有实际提问时不需要你填写审批。')}</small>${a.kind === 'employee-watch' ? '<p class="form-hint">这是停工/失联告警，先让上司核查和联系员工，不自动淘汰。</p>' : ''}${a.owner_review ? '<p class="form-hint">保留员工和证据，不自动淘汰。请核对具体缺陷与缺失凭证。</p>' : ''}<div class="alert-actions"><button type="button" class="alert-action" data-review="${esc(a.employee_id)}">${a.owner_review ? '查看不合格项与证据' : '查看报告'}</button><button type="button" class="alert-action" data-supervisor-review="${esc(a.employee_id)}" ${data.supervisor_runtime?.can_chat ? '' : 'disabled'}>${a.owner_review ? '让上司说明并提请决定' : '请上司处理'}</button></div></div>`).join('') || '<div class="empty">当前没有台账待处理事项。</div>';
    $('alerts').querySelectorAll('[data-review]').forEach(b => b.addEventListener('click', () => showEmployee(b.dataset.review)));
    $('alerts').querySelectorAll('[data-supervisor-review]').forEach(b => b.addEventListener('click', () => requestSupervisorReview(b.dataset.supervisorReview, b)));
  }
  function qcReviewMarkup(review) {
    if(!review) return '';
    const text=value=>typeof value==='string'?value:JSON.stringify(value);
    const findings=(review.findings || []).map(f=>`<li><strong>${esc(f.title || f.summary || f.id || '未命名问题')}</strong><p>${esc(label(f.status || 'not-classified'))} · 严重程度：${esc(f.severity || review.severity || '未分类')}</p>${['requirement','expected','observed','evidence','evidence_path','repair','recommendation','impact'].filter(key=>f[key]!=null).map(key=>`<p>${esc(({requirement:'要求依据',expected:'预期',observed:'实测',evidence:'证据',evidence_path:'证据路径',repair:'修复',recommendation:'建议',impact:'影响'})[key])}：${esc(text(f[key]))}</p>`).join('')}</li>`).join('');
    return `<section class="qc-owner-review"><h3>QC-${esc(review.round)} 返修复检：需要你审核</h3><p>${esc(review.notice)}</p><p>${esc(review.summary)}</p><h4>检查状态（保留原报告）</h4><p>${Object.entries(review.quality_checks || {}).map(([id,status])=>`${esc(id)}：${esc(label(status))}`).join(' · ') || '报告没有逐项状态，请让上司补充说明。'}</p><h4>已修复/已关闭</h4><ul>${(review.resolved_items || []).map(item=>`<li>${esc(item)}</li>`).join('') || '<li>报告尚未列出关闭项。</li>'}</ul><h4>剩余不合格或待验证项</h4><ul>${findings || '<li>报告没有结构化条目；请核对原始报告和上司说明。</li>'}</ul><h4>证据身份</h4><p>${esc(label(review.report_integrity))}<br>报告：${esc(review.report_path)}<br>SHA-256：${esc(review.report_sha256)}</p><p>你可以接受指定例外（原因可空）、授权继续返修/复检，或明确淘汰。上司必须通过真实 question 等待你的选择，不能默认淘汰，也不能把缺失证据写成已通过。</p></section>`;
  }
  function renderActivity(rows) {
    events = rows;
    $('activity').innerHTML = (expanded ? rows : rows.slice(-7)).slice().reverse().map(e => `<div class="activity-item"><i class="activity-dot"></i><div><strong title="${esc(e.event)}">${esc(EVENTS[e.event] || '生产事件')}</strong><small>${esc(e.employee_id || e.supervisor_id || '系统')} · ${esc(label(e.status))}</small></div><span class="activity-time">${ago(e.recorded_at)}</span></div>`).join('') || '<div class="empty">暂无活动记录。</div>';
    $('activity-all').textContent = expanded ? '收起' : '查看全部';
  }
  function renderMessages(rows, id, empty) {
    const box = $(id); const wasBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
    box.innerHTML = rows.map(m => `<div class="message-bubble ${m.type === 'user' || m.sender_type === 'owner' ? 'owner' : ''} ${m.text?.startsWith('[工具调用') ? 'tool' : ''}"><div class="message-meta">${esc(label(m.sender_type || m.type))} ${esc(m.agent || m.sender_id || '')} · ${ago(m.created_at)}</div><p>${esc(m.text)}</p></div>`).join('') || `<div class="empty">${esc(empty)}</div>`;
    if (wasBottom) box.scrollTop = box.scrollHeight;
  }
  function openTab(tab) {
    document.querySelectorAll('.inspector-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    document.querySelectorAll('.inspector-body').forEach(body => body.classList.toggle('hidden', body.id !== (tab === 'evidence' ? 'detail-evidence-tab' : `detail-${tab}`)));
    if (tab === 'questions') renderDetailQuestions();
  }
  function closeInspector() {selection = null; detailForms=[]; selectionEpoch++; $('inspector').classList.add('hidden'); $('inspector').setAttribute('aria-hidden','true');}
  function prepareEmployeeInspector(id, tab) {
    detailForms=[]; nativeForms=globalForms;
    const known = data?.employees?.find(e => e.employee_id === id) || null;
    selection = {kind:'employee', id, employee:known || {employee_id:id, status:'loading'}, sessionId:known?.session_id};
    $('inspector').classList.remove('hidden'); $('inspector').setAttribute('aria-hidden','false');
    $('detail-title').textContent = id; $('detail-role').textContent = known ? `${label(known.role)} · ${label(known.transport)}` : '正在读取员工详情…';
    $('detail-avatar').textContent = known ? label(known.role).slice(0,1) : '…'; $('detail-status').textContent = known ? label(known.status) : '加载中';
    $('detail-state').innerHTML = '<h3>正在读取实际状态</h3><p>点击已收到，正在读取该员工及其原生问题。没有创建新的审批或普通消息。</p>';
    $('detail-evidence').innerHTML = '<h3>证据</h3><p>读取中…</p>';
    $('detail-json').textContent = ''; $('detail-log').textContent = '';
    $('start-worker').disabled = true; $('dispatch-worker').disabled = true; $('fire-worker').disabled = true;
    $('employee-chat-input').disabled = true; $('employee-chat-send').disabled = true;
    $('employee-chat-input').value = ''; $('detail-messages').textContent = '';
    $('employee-chat-status').textContent = '正在核验实际会话；未核验前不会发送消息。';
    openTab(tab);
  }
  async function showEmployee(id, tab = 'overview') {
    const capturedScope = scope; const epoch = ++selectionEpoch;
    prepareEmployeeInspector(id, tab);
    try {
      const value = await api(`/api/dashboard?${query({employee_id:id})}`);
      if (scope !== capturedScope || selectionEpoch !== epoch) return;
      const employee = value.employees?.[0];
      if (!employee) {
        $('detail-status').textContent = '未找到';
        $('detail-state').innerHTML = `<h3>没有返回该员工</h3><p>${esc(id)} 不在当前 supervisor 的可见员工树中。请刷新工作区后重试。</p>`;
        $('employee-chat-status').textContent = '详情读取失败，未发送任何消息。';
        return;
      }
      selection.employee = employee; selection.sessionId = employee.session_id;
      $('detail-title').textContent = id; $('detail-role').textContent = `${label(employee.role)} · ${label(employee.transport)}`;
      $('detail-avatar').textContent = label(employee.role).slice(0,1); $('detail-status').textContent = label(employee.status);
      $('detail-state').innerHTML = `<h3>当前状态</h3><p>${esc(action(employee))}</p><p>阶段：${esc(label(employee.phase))}<br>实际会话：${esc(employee.session_id || '未绑定')}<br>会话状态：${esc(label(employee.runtime?.status))}<br>心跳：${esc(label(employee.health?.status))}<br>上级：${esc(label(employee.parent_id))}<br>独占目录：${esc(employee.job_root)}</p>${employee.health?.attention ? `<p class="health-warning">${esc(employee.health.reason)}<br>最后报告：${esc(ago(employee.health.last_report_at))}</p>` : ''}${stageMarkup(employee)}`;
      $('detail-evidence').innerHTML = `<h3>当前证据</h3><p>质检：${esc(label(employee.qc?.decision))}<br>模型试跑：${esc(label(employee.rollout_gate?.status))}<br>最近报告：${esc(label(employee.report?.status))}</p>${qcReviewMarkup(employee.qc_owner_review)}`;
      $('detail-json').textContent = JSON.stringify(employee,null,2); $('detail-log').textContent = employee.report?.summary || '暂无员工报告。';
      $('start-worker').hidden = false; $('dispatch-worker').hidden = false; $('fire-worker').hidden = false;
      const startable = Boolean(data?.communication_control?.worker_channel_open && data?.runtime_capabilities?.bound_session_resume && employee.session_id && !['fired','complete','needs-owner-decision'].includes(employee.status));
      $('start-worker').disabled = !startable;
      $('start-worker').title = startable ? '向此员工的已有绑定 session 提交真实 resume prompt；等待 heartbeat 后才算运行' : '需要开启上下级通道、实际 runner 能力和已绑定 session';
      $('dispatch-worker').disabled = !data?.supervisor_runtime?.can_chat || ['fired','complete'].includes(employee.status);
      $('fire-worker').disabled = !data?.supervisor_runtime?.can_chat;
      const canChat=employee.runtime?.can_chat && data?.communication_control?.worker_channel_open !== false;
      $('employee-chat-input').disabled = !canChat; $('employee-chat-send').disabled = !canChat;
      $('employee-chat-input').value = ''; $('detail-messages').textContent = '';
      $('employee-chat-status').textContent = canChat ? '消息提交到此员工的实际会话；原生问题请在“待回答”中选择。' : data?.communication_control?.worker_channel_open === false ? '负责人已关闭上下级通道；员工消息暂不投递，可直接联系上司。' : '未核验可操纵会话，不能直接发送。';
      openTab(tab);
      await Promise.all([
        tab === 'chat' ? refreshChat() : Promise.resolve(),
        refreshQuestions(true),
      ]);
    } catch (error) {
      if (epoch === selectionEpoch) {
        $('detail-status').textContent = '读取失败';
        $('detail-state').innerHTML = `<h3>读取失败</h3><p>${esc(error.message)}</p>`;
        $('employee-chat-status').textContent = '没有发送消息；请稍后重试。';
        $('connection').textContent = error.message;
      }
    }
  }
  async function showSession(id) {
    const isSupervisor = data?.supervisor_runtime?.session_id === id;
    const session = isSupervisor ? data.supervisor_runtime : data?.opencode_sessions?.sessions?.find(s => s.session_id === id);
    if (!session) return;
    selectionEpoch++;
    detailForms = [];
    selection = {kind:isSupervisor ? 'supervisor' : 'session', id, sessionId:id};
    $('inspector').classList.remove('hidden'); $('inspector').setAttribute('aria-hidden','false');
    $('detail-title').textContent = session.title; $('detail-role').textContent = `${label(session.agent)} · ${id}`;
    $('detail-avatar').textContent = '会'; $('detail-status').textContent = label(session.status);
    $('detail-state').innerHTML = `<h3>真实 OpenCode 会话</h3><p>状态：${esc(label(session.status))}<br>父会话：${esc(session.parent_id || '根会话')}<br>目录：${esc(session.directory)}</p>`;
    $('detail-evidence').innerHTML = `<h3>会话身份</h3><p>Agent：${esc(label(session.agent))}<br>执行结果：${esc(label(session.outcome))}</p>`;
    $('detail-json').textContent = JSON.stringify(session,null,2); $('detail-log').textContent = '消息正文与工具调用名称保留原文，见“对话”。';
    $('start-worker').hidden = true; $('dispatch-worker').hidden = true; $('fire-worker').hidden = true;
    $('employee-chat-input').disabled = false; $('employee-chat-send').disabled = false; $('employee-chat-input').value = '';
    $('detail-messages').textContent = ''; $('employee-chat-status').textContent = '已选中此会话。查看不会发消息；发送消息和回答原生问题是两种不同操作。';
    openTab('chat'); await Promise.all([refreshChat(), refreshQuestions(true)]);
  }
  async function refreshChat() {
    if (!selection?.sessionId || $('inspector').classList.contains('hidden')) return;
    const current = selection, epoch = selectionEpoch, currentScope = scope;
    const path = current.kind === 'supervisor' ? `/api/supervisor-session?${query({})}` : current.kind === 'employee' ? `/api/employee-session?${query({employee_id:current.id})}` : `/api/opencode-session?${query({session_id:current.id})}`;
    try {
      const value = await api(path);
      if (epoch !== selectionEpoch || currentScope !== scope) return;
      renderMessages(value.messages || [], 'detail-messages', '暂无可显示的正文。');
      $('employee-chat-status').textContent = `已选会话：${value.session_id} · ${value.status === 'partial' ? '部分加载；请稍后刷新' : '正文已加载'}`;
    } catch(error) {if (epoch === selectionEpoch) $('employee-chat-status').textContent=error.message;}
  }

  async function submitNative(form, answer) {
    const capturedScope = scope;
    const value = await api('/api/opencode-form-reply', {method:'POST', body:JSON.stringify({
      supervisor_id:capturedScope, session_id:form.session_id, form_id:form.form_id, answer,
      intent:selection?.kind === 'session' ? 'runtime' : 'production',
    })});
    globalForms = globalForms.filter(f => f.form_id !== form.form_id);
    detailForms = detailForms.filter(f => f.form_id !== form.form_id);
    $('native-answer-result').textContent = value.note || '回答已交给正在等待的 Agent；尚不代表后续工作完成。请查看上司回复与工具调用。';
    setConnection('回答已交给真实等待工具，执行结果待上司反馈');
    await Promise.all([refreshQuestions(true), refresh(), supervisorMessages()]);
    return value;
  }
  function renderDetailQuestions() {
    const box=$('detail-question-list');
    const questions = nativeForms.filter(f => {
      if (!selection) return false;
      if (selection.kind === 'employee') {
        const employeeId = f.metadata?.edgebench?.employee_id;
        return employeeId === selection.id || f.session_id === data?.supervisor_runtime?.session_id || Boolean(selection.sessionId && f.session_id === selection.sessionId);
      }
      return f.session_id === selection.sessionId;
    });
    const live = questions.filter(f => f.can_reply);
    $('detail-question-status').textContent = live.length ? `${live.length} 个 Agent 问题；上司问题以其原文对象为准` : '没有 Agent 正在等待你的回答；阶段告警不等于负责人审批。';
    window.OpenCodeDecisions.sync(box, live, submitNative);
    box.querySelector('.ledger-question-note')?.remove();
    if (selection?.kind === 'employee') {
      const note = document.createElement('div');
      note.className = 'ledger-question-note';
      const waiting = ['needs-owner-decision','replacement-required','repair-required','rollout-pending','awaiting-approval'].includes(selection.employee.status);
      note.textContent = live.length
        ? `台账：${label(selection.employee.status)}。回答后同一 Agent 继续；查看其实际回复和工具调用，不把“已提交”当成“已执行”。`
        : waiting
          ? `台账：${label(selection.employee.status)}。先由上司审查阶段报告。旧 Form 收据不能放行生产，可点击下方“请上司审查此员工”。`
          : `员工台账状态：${label(selection.employee.status)}。这里只显示 OpenCode 原生问题。`;
      box.append(note);
    }
  }
  async function refreshQuestions(selectionOnly = false) {
    if (!scope || (selectionOnly && !selection)) return;
    const currentScope=scope, currentSelection=selection, epoch=selectionEpoch;
    const fields = selectionOnly ? currentSelection.kind === 'employee' ? {employee_id:currentSelection.id} : {session_id:currentSelection.sessionId} : {};
    const path = `/api/opencode-forms?${query(fields)}`;
    try {
      if (!questionRequests.has(path)) questionRequests.set(path, api(path).finally(() => questionRequests.delete(path)));
      const value=await questionRequests.get(path);
      if (scope !== currentScope || (selectionOnly && epoch !== selectionEpoch)) return;
      if(value.status!=='ok') throw new Error(value.reason || '原生问题查询失败。');
      if(selectionOnly) detailForms=value.forms || []; else globalForms=value.forms || [];
      nativeForms=[...new Map([...globalForms, ...detailForms].map(f => [f.form_id,f])).values()];
      const live = globalForms.filter(f => f.can_reply);
      const detached = globalForms.length - live.length;
      $('native-question-count').textContent=`${live.length} 个实际问题`;
      $('native-question-note').textContent=detached ? `${detached} 个表单未核验到等待中的 Agent，不能用于批准生产。` : live.length ? '回答会恢复正在等待的 question 工具；执行结果查看同一上司会话。' : '当前没有 Agent 在等你的回答。下方阶段报告先由上司处理，不需要填写通用接受/淘汰表单。';
      window.OpenCodeDecisions.sync($('native-question-list'),live,submitNative);
      if(selection) renderDetailQuestions();
    } catch(error) {if(scope===currentScope){$('native-question-count').textContent='查询失败'; $('native-question-note').textContent=error.message;}}
  }

  async function refresh() {
    if (!scope || updating) return;
    updating=true; const captured=scope;
    try {
      const [dashboard,activity]=await Promise.all([api(`/api/dashboard?${query({})}`),api(`/api/events?${query({})}`)]);
      if(captured!==scope) return;
      renderDashboard(dashboard); renderActivity(activity.events || []); setConnection('已连接');
    } catch(error) {if(captured===scope) setConnection(error.message,true);}
    finally {updating=false;}
  }
  function connect() {
    if(stream) stream.close();
    if(!scope) return;
    const captured=scope;
    const p=query({}); if(token) p.set('token',token);
    stream=new EventSource(`/api/stream?${p}`);
    stream.addEventListener('dashboard',event=>{
      if(captured!==scope) return;
      try {renderDashboard(JSON.parse(event.data)); setConnection('实时连接');} catch {setConnection('实时数据格式错误',true);}
    });
    stream.onerror=()=>{if(captured===scope) setConnection('正在重连',true);};
  }
  async function switchWorkspace(id) {
    if(!id || scope===id) return;
    scope=id; data=null; globalForms=[]; detailForms=[]; nativeForms=[];
    window.OpenCodeDecisions.sync($('native-question-list'), [], submitNative);
    $('native-answer-result').textContent='';
    closeInspector(); $('command-modal').classList.add('hidden');
    history.replaceState(null,'',`?supervisor_id=${encodeURIComponent(id)}`);
    renderWorkspaces(); connect(); await refresh(); await refreshQuestions();
  }
  async function supervisorMessages() {
    if(!scope || supervisorChatLoading) return;
    supervisorChatLoading=true;
    const captured=scope;
    try {const value=await api(`/api/supervisor-session?${query({})}`);
      if(captured===scope) {
        renderMessages(value.messages || [],'supervisor-messages','暂无真实上司回复。');
        $('command-binding').textContent=`实际上司：${value.session_id} · ${label(value.runtime?.status)}。发送会唤醒此会话；原生问题请单独回答，接收不代表执行。`;
      }
    } catch(error) {$('command-result').textContent=error.message;}
    finally {supervisorChatLoading=false;}
  }
  function openSupervisorChat() { $('command-modal').classList.remove('hidden'); supervisorMessages(); }
  async function requestSupervisorReview(id, button) {
    if(!data?.supervisor_runtime?.can_chat) return;
    if(!window.confirm(`将 ${id} 的报告交给真实上司审查？这会唤醒其会话，但不批准试跑、QC 例外或淘汰。`)) return;
    const captured=scope;
    if(button) button.disabled=true;
    try {
      const result=await api('/api/supervisor-review',{method:'POST',body:JSON.stringify({supervisor_id:captured,employee_id:id})});
      if(captured!==scope) return;
      $('command-result').textContent=`已接收 ${result.inbox_id || result.message_id}；等待真实回复与操作回执。`;
      openSupervisorChat();
    } catch(error) {$('connection').textContent=error.message;}
    finally {if(button) button.disabled=false;}
  }

  $('refresh').addEventListener('click',()=>{refresh();refreshQuestions();});
  $('close-detail').addEventListener('click',closeInspector);
  document.querySelector('.inspector-backdrop').addEventListener('click',closeInspector);
  document.querySelectorAll('.inspector-tab').forEach(b=>b.addEventListener('click',()=>openTab(b.dataset.tab)));
  $('supervisor-inspect').addEventListener('click',()=>showSession(data?.supervisor_runtime?.session_id));
  async function ownerControl(action) {
    if(!scope) return;
    if(!window.confirm(action==='pause' ? '关闭本批次自动上下级投递，并中断非提问中的上司？员工和证据保留，负责人消息仍可发送。' : '恢复上下级通道？尚未决定的提案/原生问题仍保持关闭；不会重放历史消息或自动批准候选。')) return;
    const captured=scope;
    $('owner-pause').disabled=true; $('owner-resume').disabled=true;
    try {
      const value=await api('/api/owner-control',{method:'POST',body:JSON.stringify({supervisor_id:captured,action})});
      if(captured===scope) {
        setConnection(value.worker_channel_open ? '负责人允许恢复通道；未启动员工' : '上下级通道已关闭，请直接联系上司');
        await refresh();
      }
    } catch(error) {setConnection(error.message,true);await refresh();}
  }
  $('owner-pause').addEventListener('click',()=>ownerControl('pause'));
  $('owner-resume').addEventListener('click',()=>ownerControl('resume'));
  $('command-open').addEventListener('click',openSupervisorChat);
  $('command-close').addEventListener('click',()=>$('command-modal').classList.add('hidden'));
  $('command-form').addEventListener('submit',async event=>{
    event.preventDefault(); const input=$('command-input'); if(!input.value.trim() || !scope) return;
    $('command-send').disabled=true;
    try {const value=await api('/api/message',{method:'POST',body:JSON.stringify({supervisor_id:scope,target_type:'supervisor',target_id:scope,text:input.value.trim()})}); input.value='';$('command-result').textContent=`真实会话已接收 ${value.inbox_id || value.message_id}；请等上司回复，尚无执行完成证据。`;await supervisorMessages();}
    catch(error){$('command-result').textContent=error.message;}
    finally {$('command-send').disabled=!data?.supervisor_runtime?.can_chat;}
  });
  $('employee-chat-form').addEventListener('submit',async event=>{
    event.preventDefault(); const input=$('employee-chat-input'); if(!selection || !input.value.trim() || chatSending) return;
    const current=selection, captured=scope, epoch=selectionEpoch;
    if(!window.confirm(`将普通消息发送给 ${current.id}？原生问题请改用“待回答”。`)) return;
    chatSending=true; $('employee-chat-send').disabled=true;
    try {await api(current.kind==='supervisor'?'/api/message':current.kind==='employee'?'/api/employee-session-message':'/api/opencode-message',{method:'POST',body:JSON.stringify({supervisor_id:captured,employee_id:current.kind==='employee'?current.id:undefined,session_id:current.sessionId,text:input.value.trim(),target_type:current.kind==='supervisor'?'supervisor':undefined,target_id:current.kind==='supervisor'?captured:undefined})});
      if(epoch===selectionEpoch){input.value='';$('employee-chat-status').textContent='普通消息已提交真实会话，等待回复。';await refreshChat();}}
    catch(error){if(epoch===selectionEpoch) $('employee-chat-status').textContent=error.message;}
    finally {chatSending=false;if(epoch===selectionEpoch)$('employee-chat-send').disabled=false;}
  });
  $('dispatch-worker').addEventListener('click',async()=>{
    if(selection?.kind!=='employee') return;
    await requestSupervisorReview(selection.id, $('dispatch-worker'));
  });
  $('start-worker').addEventListener('click',async()=>{
    if(selection?.kind!=='employee' || !scope) return;
    const button=$('start-worker');button.disabled=true;
    try {
      const value=await api('/api/start',{method:'POST',body:JSON.stringify({supervisor_id:scope,employee_id:selection.id,note:'owner requested Web resume'})});
      $('employee-chat-status').textContent=`已提交真实恢复 ${value.inbox_id || value.status}；等待员工 heartbeat，暂不声称已运行。`;
      await refresh(); await showEmployee(selection.id);
    } catch(error) { $('employee-chat-status').textContent=error.message; button.disabled=false; }
  });
  $('fire-worker').addEventListener('click',openSupervisorChat);
  document.querySelectorAll('.view-switch').forEach(b=>b.addEventListener('click',()=>{view=b.dataset.view;document.querySelectorAll('.view-switch').forEach(x=>x.classList.toggle('active',x===b));renderEmployees();}));
  $('filter-button').addEventListener('click',()=>{filter=filter==='all'?'attention':filter==='attention'?'active':'all';$('filter-button').textContent=`筛选：${{all:'全部',attention:'待处理',active:'未封存'}[filter]}`;renderEmployees();});
  $('activity-all').addEventListener('click',()=>{expanded=!expanded;renderActivity(events);});
  $('theme-toggle').addEventListener('click',()=>document.body.classList.toggle('dark-mode'));
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeInspector();$('command-modal').classList.add('hidden');}});
  window.setInterval(()=>{if(document.hidden)return;refreshQuestions();if(selection)refreshQuestions(true);if(document.querySelector('.inspector-tab.active')?.dataset.tab==='chat')refreshChat();if(!$('command-modal').classList.contains('hidden'))supervisorMessages();},5000);
  (async()=>{
    try {const value=await api('/api/supervisors');supervisors=value.supervisors || [];
      if(!supervisors.some(s=>s.supervisor_id===scope))scope=(supervisors.find(s=>s.workspace?.lock_state==='REQUIREMENTS_LOCKED') || supervisors[0])?.supervisor_id;
      renderWorkspaces(); await refresh(); connect(); await refreshQuestions();
    } catch(error){setConnection(error.message,true);}
  })();
})();
