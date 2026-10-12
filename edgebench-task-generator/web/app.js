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
    'owner':'你', 'supervisor':'上司', 'employee':'员工', 'user':'你', 'assistant':'智能体',
    'opencode-task':'OpenCode 会话', 'orca-cli':'Orca 编排',
    'employee-state':'员工事项', 'rollout-gate':'试跑检查',
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
  let formsRefreshing = false;
  let formsFingerprint = '';
  let nativeForms = [];
  let chatSending = false;

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
    $('alert-note').textContent = value.alerts?.length ? '需要负责人处理' : '暂无告警';
    $('health').textContent = value.opencode_sessions?.status === 'unavailable' ? '运行时不可用' : '已连接';
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
      return `<article class="agent-card" style="--card-tint:${bad ? '#fbe7e5' : warn ? '#fff0d9' : '#e3f3e9'}"><div class="card-top"><div class="agent-identity"><span class="agent-avatar">${esc(label(e.role).slice(0,1))}</span><div><strong title="${esc(e.employee_id)}">${esc(e.employee_id)}</strong><small>${esc(label(e.role))} · ${esc(label(e.transport))}</small></div></div><span title="${esc(e.status)}" class="card-status ${bad ? 'bad' : warn ? 'warn' : ''}"><i></i>${esc(label(e.status))}</span></div><div class="card-body"><div class="card-action">当前 <b>${esc(action(e))}</b></div><small>阶段：${esc(label(e.phase))} · 会话：${esc(label(e.runtime?.status))}</small>${stageMarkup(e)}</div><div class="card-footer"><span title="${esc(e.session_id || '')}">${esc(e.session_id || e.seed_id || '未绑定会话')}</span><button type="button" data-employee="${esc(e.employee_id)}">查看详情 →</button></div></article>`;
    }).join('') || '<div class="empty">当前筛选没有员工；临时 OpenCode 会话不会自动成为出题员工。</div>';
    $('agent-grid').querySelectorAll('[data-employee]').forEach(b => b.addEventListener('click', () => showEmployee(b.dataset.employee)));
  }
  function renderSessions(snapshot) {
    const sessions = snapshot.sessions || [];
    $('session-summary').textContent = snapshot.status === 'unavailable' ? 'OpenCode 运行时不可用' : `全局 · 运行 ${snapshot.active_count ?? 0} 个 · 共 ${sessions.length} 个`;
    $('session-grid').innerHTML = sessions.map(s => `<button type="button" class="session-card ${s.status === 'running' ? 'running' : ''}" data-session="${esc(s.session_id)}" title="查看此会话，不会发送消息"><i class="session-dot"></i><div><strong>${esc(s.title)}</strong><small>${esc(label(s.agent))} · ${esc(s.session_id)}${s.parent_id ? ` · 父会话 ${esc(s.parent_id)}` : ''}</small></div><span class="session-badge">${esc(label(s.status))}</span></button>`).join('') || `<div class="empty">${snapshot.status === 'unavailable' ? '无法查询真实会话，请检查 OpenCode 服务。' : '当前扫描范围没有会话。'}</div>`;
    $('session-grid').querySelectorAll('[data-session]').forEach(b => b.addEventListener('click', () => showSession(b.dataset.session)));
  }
  function renderAlerts() {
    const alerts = data?.alerts || [];
    const seen = new Set();
    $('alerts').innerHTML = alerts.filter(a => {const key=`${a.employee_id}:${a.status}`; if(seen.has(key)) return false; seen.add(key); return true;}).map(a => `<div class="attention-item"><strong>${esc(a.employee_id)} · ${esc(label(a.status))}</strong><small>${esc(data.employees?.find(e => e.employee_id === a.employee_id)?.report?.summary || '请核对原始提问和证据。')}</small><div class="alert-actions"><button type="button" class="alert-action" data-review="${esc(a.employee_id)}">查看并决定</button></div></div>`).join('') || '<div class="empty">当前没有台账待处理事项。原生提问请查看上方模块。</div>';
    $('alerts').querySelectorAll('[data-review]').forEach(b => b.addEventListener('click', () => showEmployee(b.dataset.review, 'questions')));
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
  function closeInspector() {selection = null; selectionEpoch++; $('inspector').classList.add('hidden'); $('inspector').setAttribute('aria-hidden','true');}
  async function showEmployee(id, tab = 'overview') {
    const capturedScope = scope; const epoch = ++selectionEpoch;
    try {
      const value = await api(`/api/dashboard?${query({employee_id:id})}`);
      if (scope !== capturedScope || selectionEpoch !== epoch) return;
      const employee = value.employees?.[0]; if (!employee) return;
      selection = {kind:'employee', id, employee, sessionId:employee.session_id};
      $('inspector').classList.remove('hidden'); $('inspector').setAttribute('aria-hidden','false');
      $('detail-title').textContent = id; $('detail-role').textContent = `${label(employee.role)} · ${label(employee.transport)}`;
      $('detail-avatar').textContent = label(employee.role).slice(0,1); $('detail-status').textContent = label(employee.status);
      $('detail-state').innerHTML = `<h3>当前状态</h3><p>${esc(action(employee))}</p><p>阶段：${esc(label(employee.phase))}<br>实际会话：${esc(employee.session_id || '未绑定')}<br>会话状态：${esc(label(employee.runtime?.status))}<br>上级：${esc(label(employee.parent_id))}<br>独占目录：${esc(employee.job_root)}</p>${stageMarkup(employee)}`;
      $('detail-evidence').innerHTML = `<h3>当前证据</h3><p>质检：${esc(label(employee.qc?.decision))}<br>模型试跑：${esc(label(employee.rollout_gate?.status))}<br>最近报告：${esc(label(employee.report?.status))}</p>`;
      $('detail-json').textContent = JSON.stringify(employee,null,2); $('detail-log').textContent = employee.report?.summary || '暂无员工报告。';
      $('dispatch-worker').hidden = false; $('fire-worker').hidden = false;
      $('dispatch-worker').disabled = !['active','queued','repair-required'].includes(employee.status);
      $('fire-worker').disabled = employee.status === 'fired';
      $('employee-chat-input').disabled = !employee.runtime?.can_chat; $('employee-chat-send').disabled = !employee.runtime?.can_chat;
      $('employee-chat-input').value = ''; $('detail-messages').textContent = '';
      $('employee-chat-status').textContent = employee.runtime?.can_chat ? '消息提交到此员工的实际会话；原生问题请在“待回答”中选择。' : '未核验可操纵会话，不能直接发送。';
      openTab(tab);
      if (employee.runtime?.can_chat) await refreshChat();
      await refreshQuestions();
    } catch (error) {if (epoch === selectionEpoch) $('connection').textContent = error.message;}
  }
  async function showSession(id) {
    const session = data?.opencode_sessions?.sessions?.find(s => s.session_id === id);
    if (!session) return;
    selectionEpoch++;
    selection = {kind:'session', id, sessionId:id};
    $('inspector').classList.remove('hidden'); $('inspector').setAttribute('aria-hidden','false');
    $('detail-title').textContent = session.title; $('detail-role').textContent = `${label(session.agent)} · ${id}`;
    $('detail-avatar').textContent = '会'; $('detail-status').textContent = label(session.status);
    $('detail-state').innerHTML = `<h3>真实 OpenCode 会话</h3><p>状态：${esc(label(session.status))}<br>父会话：${esc(session.parent_id || '根会话')}<br>目录：${esc(session.directory)}</p>`;
    $('detail-evidence').innerHTML = `<h3>会话身份</h3><p>Agent：${esc(label(session.agent))}<br>执行结果：${esc(label(session.outcome))}</p>`;
    $('detail-json').textContent = JSON.stringify(session,null,2); $('detail-log').textContent = '消息正文与工具调用名称保留原文，见“对话”。';
    $('dispatch-worker').hidden = true; $('fire-worker').hidden = true;
    $('employee-chat-input').disabled = false; $('employee-chat-send').disabled = false; $('employee-chat-input').value = '';
    $('detail-messages').textContent = ''; $('employee-chat-status').textContent = '已选中此会话。查看不会发消息；发送消息和回答原生问题是两种不同操作。';
    openTab('chat'); await refreshChat(); await refreshQuestions();
  }
  async function refreshChat() {
    if (!selection?.sessionId || $('inspector').classList.contains('hidden')) return;
    const current = selection, epoch = selectionEpoch, currentScope = scope;
    const path = current.kind === 'employee' ? `/api/employee-session?${query({employee_id:current.id})}` : `/api/opencode-session?${query({session_id:current.id})}`;
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
    })});
    nativeForms = nativeForms.filter(f => f.form_id !== form.form_id);
    formsFingerprint = ''; setConnection('原生回答已提交');
    return value;
  }
  function renderDetailQuestions() {
    const box=$('detail-question-list'); box.replaceChildren();
    const questions = nativeForms.filter(f => f.session_id === selection?.sessionId);
    $('detail-question-status').textContent = questions.length ? `${questions.length} 个原生问题待回答` : '此会话暂无原生待回答问题；不会从员工状态伪造审批选项。';
    for (const f of questions) window.OpenCodeDecisions.mount(box,f,submitNative);
    if (selection?.kind === 'employee') {
      const note = document.createElement('div');
      note.className = 'ledger-question-note';
      note.textContent = `员工台账状态：${label(selection.employee.status)}。这里只显示 OpenCode 原生问题；审批选项必须由上司会话创建，不在这里伪造。`;
      box.append(note);
    }
  }
  async function refreshQuestions() {
    if (!scope || formsRefreshing) return;
    formsRefreshing=true; const currentScope=scope;
    try {
      const value=await api(`/api/opencode-forms?${query({})}`);
      if (scope !== currentScope) return;
      $('native-question-count').textContent=value.status==='ok' ? `${value.forms?.length || 0} 个待回答` : '暂不可用';
      if(value.status!=='ok') {$('native-question-note').textContent=value.reason || '原生问题查询失败。'; return;}
      $('native-question-note').textContent='选项来自 OpenCode 原生提问。选择或自填后核对、确认提交，不发送普通聊天消息。';
      const fingerprint=JSON.stringify(value.forms || []);
      if(fingerprint===formsFingerprint) return;
      nativeForms=value.forms || []; formsFingerprint=fingerprint;
      $('native-question-list').replaceChildren();
      for(const f of nativeForms) window.OpenCodeDecisions.mount($('native-question-list'),f,submitNative);
      if(!nativeForms.length) $('native-question-list').textContent='当前没有原生问题。新问题出现后会自动显示；也可点开会话进入“待回答”。';
      if(selection) renderDetailQuestions();
    } catch(error) {if(scope===currentScope){$('native-question-count').textContent='查询失败'; $('native-question-note').textContent=error.message;}}
    finally {formsRefreshing=false;}
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
    scope=id; data=null; formsFingerprint=''; nativeForms=[];
    closeInspector(); $('command-modal').classList.add('hidden');
    history.replaceState(null,'',`?supervisor_id=${encodeURIComponent(id)}`);
    renderWorkspaces(); connect(); await refresh(); await refreshQuestions();
  }
  async function supervisorMessages() {
    if(!scope) return;
    const captured=scope;
    try {const value=await api(`/api/messages?${query({target_type:'supervisor',target_id:scope})}`);
      if(captured===scope) renderMessages(value.messages || [],'supervisor-messages','暂无上司消息。');
    } catch(error) {$('command-result').textContent=error.message;}
  }

  $('refresh').addEventListener('click',()=>{refresh();refreshQuestions();});
  $('close-detail').addEventListener('click',closeInspector);
  document.querySelector('.inspector-backdrop').addEventListener('click',closeInspector);
  document.querySelectorAll('.inspector-tab').forEach(b=>b.addEventListener('click',()=>openTab(b.dataset.tab)));
  $('command-open').addEventListener('click',()=>{$('command-modal').classList.remove('hidden');supervisorMessages();});
  $('command-close').addEventListener('click',()=>$('command-modal').classList.add('hidden'));
  $('command-form').addEventListener('submit',async event=>{
    event.preventDefault(); const input=$('command-input'); if(!input.value.trim() || !scope) return;
    try {await api('/api/message',{method:'POST',body:JSON.stringify({supervisor_id:scope,target_type:'supervisor',target_id:scope,text:input.value.trim()})}); input.value='';$('command-result').textContent='消息已入队，等待上司实际读取。';await supervisorMessages();}
    catch(error){$('command-result').textContent=error.message;}
  });
  $('employee-chat-form').addEventListener('submit',async event=>{
    event.preventDefault(); const input=$('employee-chat-input'); if(!selection || !input.value.trim() || chatSending) return;
    const current=selection, captured=scope, epoch=selectionEpoch;
    if(!window.confirm(`将普通消息发送给 ${current.id}？原生问题请改用“待回答”。`)) return;
    chatSending=true; $('employee-chat-send').disabled=true;
    try {await api(current.kind==='employee'?'/api/employee-session-message':'/api/opencode-message',{method:'POST',body:JSON.stringify({supervisor_id:captured,employee_id:current.kind==='employee'?current.id:undefined,session_id:current.sessionId,text:input.value.trim()})});
      if(epoch===selectionEpoch){input.value='';$('employee-chat-status').textContent='普通消息已提交真实会话，等待回复。';await refreshChat();}}
    catch(error){if(epoch===selectionEpoch) $('employee-chat-status').textContent=error.message;}
    finally {chatSending=false;if(epoch===selectionEpoch)$('employee-chat-send').disabled=false;}
  });
  $('dispatch-worker').addEventListener('click',async()=>{
    if(selection?.kind!=='employee') return;
    try{await api('/api/dispatch',{method:'POST',body:JSON.stringify({supervisor_id:scope,employee_id:selection.id})});await refresh();await showEmployee(selection.id);}
    catch(error){$('employee-chat-status').textContent=error.message;}
  });
  $('fire-worker').addEventListener('click',()=>{if(selection?.kind==='employee')openTab('questions');});
  document.querySelectorAll('.view-switch').forEach(b=>b.addEventListener('click',()=>{view=b.dataset.view;document.querySelectorAll('.view-switch').forEach(x=>x.classList.toggle('active',x===b));renderEmployees();}));
  $('filter-button').addEventListener('click',()=>{filter=filter==='all'?'attention':filter==='attention'?'active':'all';$('filter-button').textContent=`筛选：${{all:'全部',attention:'待处理',active:'未封存'}[filter]}`;renderEmployees();});
  $('activity-all').addEventListener('click',()=>{expanded=!expanded;renderActivity(events);});
  $('theme-toggle').addEventListener('click',()=>document.body.classList.toggle('dark-mode'));
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeInspector();$('command-modal').classList.add('hidden');}});
  window.setInterval(()=>{if(document.hidden)return;refreshQuestions();refreshChat();if(!$('command-modal').classList.contains('hidden'))supervisorMessages();},5000);
  (async()=>{
    try {const value=await api('/api/supervisors');supervisors=value.supervisors || [];
      if(!supervisors.some(s=>s.supervisor_id===scope))scope=(supervisors.find(s=>s.workspace?.lock_state==='REQUIREMENTS_LOCKED') || supervisors[0])?.supervisor_id;
      renderWorkspaces(); await refresh(); connect(); await refreshQuestions();
    } catch(error){setConnection(error.message,true);}
  })();
})();
