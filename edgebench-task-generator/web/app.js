(() => {
  const params = new URLSearchParams(location.search);
  const token = params.get('token') || localStorage.getItem('edgebench-console-token');
  if (token) localStorage.setItem('edgebench-console-token', token);
  const headers = () => token ? { Authorization: `Bearer ${token}` } : {};
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
  const ago = value => {
    if (!value) return '—';
    const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
    return seconds < 60 ? `${seconds}s ago` : seconds < 3600 ? `${Math.round(seconds / 60)}m ago` : `${Math.round(seconds / 3600)}h ago`;
  };
  const api = async (path, options = {}) => {
    const response = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}), 'Content-Type': 'application/json' } });
    const value = await response.json().catch(() => ({ error: response.statusText }));
    if (!response.ok) throw new Error(value.error || response.statusText);
    return value;
  };

  let supervisors = [];
  let currentSupervisor = params.get('supervisor_id');
  let selected = null;
  let selectedSession = null;
  let stream = null;
  let lastDashboard = null;
  let viewMode = 'cards';
  let filterMode = 'all';
  let lastEvents = [];
  let activityExpanded = false;

  function roleLetter(employee) { return (employee.role || 'A').slice(0, 1).toUpperCase(); }
  function statusKind(status) {
    if (['needs-owner-decision', 'repair-required', 'replacement-required'].includes(status)) return 'warn';
    if (['fired', 'failed'].includes(status)) return 'bad';
    return 'good';
  }
  function progress(employee) {
    const state = employee.status || '';
    if (state === 'running' || state === 'rollout-running') return 62;
    if (state === 'awaiting-approval' || state === 'rollout-pending') return 78;
    if (state === 'needs-owner-decision' || state === 'repair-required') return 54;
    if (state === 'complete') return 100;
    return 31;
  }
  function currentAction(employee) {
    if (employee.report?.summary) return employee.report.summary;
    if (employee.rollout_gate?.status) return `${employee.rollout_gate.status} · ${employee.rollout_gate.action || ''}`;
    if (employee.qc?.decision) return `QC-${employee.qc.round || '?'} · ${employee.qc.decision}`;
    return employee.status || 'Waiting for dispatch';
  }
  function renderWorkspaces() {
    $('supervisor-list').innerHTML = supervisors.map(item => `<button type="button" class="workspace-item ${item.supervisor_id === currentSupervisor ? 'active' : ''}" data-supervisor="${esc(item.supervisor_id)}"><span class="workspace-icon">${esc(item.benchmark.slice(0, 1))}</span><div><strong>${esc(item.workspace?.campaign_id || item.benchmark)}</strong><small>${esc(item.workspace?.target_task_count ?? '—')} tasks · ${esc(item.workspace?.lock_state || 'unlocked')}</small></div></button>`).join('');
    document.querySelectorAll('.workspace-item').forEach(button => button.addEventListener('click', () => switchSupervisor(button.dataset.supervisor)));
  }
  function renderDashboard(value) {
    lastDashboard = value;
    const employees = value.employees || [];
    const capacity = value.capacity || {};
    $('active').textContent = capacity.active ?? employees.filter(x => !['fired', 'complete'].includes(x.status)).length;
    $('capacity').textContent = `${capacity.available ?? '—'} / ${capacity.max_concurrent ?? '—'}`;
    $('alert-count').textContent = value.alerts?.length ?? 0;
    $('alert-note').textContent = value.alerts?.length ? 'owner decision required' : 'no active alerts';
    $('health').textContent = value.alerts?.some(x => x.status === 'INFRA_FAILURE') ? 'Review' : 'Good';
    $('updated').textContent = new Date().toLocaleTimeString();
    const workspace = value.workspace || {};
    $('workspace-title').textContent = workspace.campaign_id || value.benchmark || currentSupervisor || 'Workspace';
    $('workspace-heading').textContent = `${value.benchmark || 'Your'} production floor.`;
    $('workspace-subtitle').textContent = `${employees.length} employees · target ${workspace.target_task_count ?? '—'} · profile ${workspace.profile_id || 'not locked'} · ${workspace.delivery_format || 'format pending'} · ${workspace.lock_state || 'unlocked'}`;
    $('side-status').textContent = `${employees.length} visible employees`;
    renderSessions(value.opencode_sessions || {});
    const visibleEmployees = filterMode === 'attention'
      ? employees.filter(employee => ['needs-owner-decision', 'repair-required', 'replacement-required', 'fired'].includes(employee.status) || employee.rollout_gate?.status === 'EARLY_TOO_EASY')
      : filterMode === 'active'
        ? employees.filter(employee => !['fired', 'complete'].includes(employee.status))
        : employees;
    $('agent-grid').classList.toggle('timeline-mode', viewMode === 'timeline');
    $('agent-grid').innerHTML = visibleEmployees.length ? visibleEmployees.map(renderAgentCard).join('') : '<div class="empty">No producer/QC employees in this workspace yet. OpenCode sessions above are runtime sessions, not employee records.</div>';
    document.querySelectorAll('.inspect-agent').forEach(button => button.addEventListener('click', () => showInspector(button.dataset.id)));
    renderAlerts(value.alerts || []);
  }
  function renderSessions(snapshot) {
    const sessions = snapshot.sessions || [];
    if (snapshot.status === 'unavailable') {
      $('session-summary').textContent = 'OpenCode unavailable';
      $('session-grid').innerHTML = `<div class="empty">OpenCode session adapter unavailable: ${esc(snapshot.reason || 'unknown error')}</div>`;
      return;
    }
    $('session-summary').textContent = `Global runtime · ${snapshot.active_count || 0} running · ${sessions.length} visible`;
    $('session-grid').innerHTML = sessions.length ? sessions.map(session => `<article class="session-card ${session.status === 'running' ? 'running' : ''} inspect-session" data-session-id="${esc(session.session_id)}"><i class="session-dot"></i><div><strong>${esc(session.title)}</strong><small>${esc(session.agent || 'agent')} · ${esc(session.session_id)}${session.parent_id ? ` · child of ${esc(session.parent_id)}` : ''}</small></div><span class="session-badge">${esc(session.status)}</span></article>`).join('') : '<div class="empty">No OpenCode sessions found for this workspace.</div>';
    document.querySelectorAll('.inspect-session').forEach(card => card.addEventListener('click', () => showOpenCodeSession(card.dataset.sessionId)));
  }
  function renderAgentCard(employee) {
    const kind = statusKind(employee.status);
    const report = employee.report || {};
    return `<article class="agent-card" style="--card-tint:${kind === 'bad' ? '#fbe7e5' : kind === 'warn' ? '#fff0d9' : '#e3f3e9'}">
      <div class="card-top"><div class="agent-identity"><span class="agent-avatar">${roleLetter(employee)}</span><div><strong>${esc(employee.employee_id)}</strong><small>${esc(employee.role)} · ${esc(employee.transport || 'native')}</small></div></div><span class="card-status ${kind}"><i></i>${esc(employee.status || 'unknown')}</span></div>
      <div class="card-body"><div class="card-action">Now <b>${esc(currentAction(employee))}</b></div><div class="card-progress"><span style="width:${progress(employee)}%"></span></div></div>
      <div class="card-footer"><span>${esc(employee.task_id || employee.seed_id || 'No task assigned')}</span><button class="inspect-agent" data-id="${esc(employee.employee_id)}">Open inspector →</button></div>
    </article>`;
  }
  function renderAlerts(alerts) {
    $('alerts').innerHTML = alerts.length ? alerts.slice(0, 5).map(item => `<div class="attention-item"><strong>${esc(item.employee_id)} · ${esc(item.status)}</strong><small>${esc(item.action || 'Owner decision required')}</small></div>`).join('') : '<div class="empty">Everything is moving normally.</div>';
  }
  function renderActivity(events) {
    lastEvents = events;
    const visible = activityExpanded ? events : events.slice(-7);
    $('activity').innerHTML = visible.length ? visible.slice().reverse().map(event => `<div class="activity-item"><i class="activity-dot"></i><div><strong>${esc(event.event || 'event')}</strong><small>${esc(event.employee_id || event.supervisor_id || 'system')} · ${esc(event.status || event.reason || '')}</small></div><span class="activity-time">${ago(event.recorded_at)}</span></div>`).join('') : '<div class="empty">No recent activity.</div>';
    $('activity-all').textContent = activityExpanded ? 'Collapse' : 'View all';
  }
  function renderMessages(messages, containerId, emptyText) {
    $(containerId).innerHTML = messages.length ? messages.map(message => `<div class="message-bubble ${message.sender_type === 'owner' ? 'owner' : ''}"><div class="message-meta">${esc(message.sender_id || message.sender_type)} · ${ago(message.created_at)}</div><p>${esc(message.text)}</p></div>`).join('') : `<div class="empty">${esc(emptyText)}</div>`;
    const list = $(containerId); list.scrollTop = list.scrollHeight;
  }
  async function loadMessages(targetType, targetId) {
    const value = await api(`/api/messages?supervisor_id=${encodeURIComponent(currentSupervisor)}&target_type=${encodeURIComponent(targetType)}&target_id=${encodeURIComponent(targetId)}`);
    renderMessages(value.messages || [], 'detail-messages', 'No messages yet. Start a conversation with this agent.');
  }
  async function loadSessionMessages(sessionId) {
    const value = await api(`/api/opencode-session?supervisor_id=${encodeURIComponent(currentSupervisor)}&session_id=${encodeURIComponent(sessionId)}`);
    renderMessages(value.messages || [], 'detail-messages', 'No OpenCode messages yet.');
  }
  async function refresh() {
    try {
      if (!currentSupervisor) return;
      const [dashboard, events] = await Promise.all([
        api(`/api/dashboard?supervisor_id=${encodeURIComponent(currentSupervisor)}`),
        api(`/api/events?supervisor_id=${encodeURIComponent(currentSupervisor)}`),
      ]);
      renderDashboard(dashboard); renderActivity(events.events || []);
      $('connection').innerHTML = '<i></i>Live'; $('connection').className = 'connection';
    } catch (error) { $('connection').textContent = error.message; $('connection').className = 'connection error'; }
  }
  async function loadSupervisors() {
    const value = await api('/api/supervisors');
    supervisors = value.supervisors || [];
    if (!currentSupervisor || !supervisors.some(item => item.supervisor_id === currentSupervisor)) currentSupervisor = supervisors[0]?.supervisor_id;
    renderWorkspaces();
  }
  async function showInspector(id) {
    try {
      const value = await api(`/api/dashboard?supervisor_id=${encodeURIComponent(currentSupervisor)}&employee_id=${encodeURIComponent(id)}`);
      selected = value.employees?.[0]; if (!selected) return;
      selectedSession = null;
      $('inspector').classList.remove('hidden'); $('inspector').setAttribute('aria-hidden', 'false');
      $('detail-title').textContent = selected.employee_id; $('detail-role').textContent = `${selected.role} · ${selected.transport || 'native'} · ${selected.task_id || selected.seed_id || 'unassigned'}`;
      $('detail-avatar').textContent = roleLetter(selected); $('detail-status').textContent = selected.status || 'unknown';
      $('detail-state').innerHTML = `<h3>Current state</h3><p><b>${esc(currentAction(selected))}</b></p><p class="muted-line">Parent: ${esc(selected.parent_id || 'supervisor')}<br>Job root: ${esc(selected.job_root || '—')}</p>`;
      $('detail-evidence').innerHTML = `<h3>Latest evidence</h3><p>QC: ${esc(selected.qc?.decision || '—')}<br>Rollout: ${esc(selected.rollout_gate?.status || '—')}<br>Report: ${esc(selected.report?.status || '—')}</p>`;
      $('detail-json').textContent = JSON.stringify(selected, null, 2); $('detail-log').textContent = selected.report?.summary || 'No worker summary has been reported yet.';
      $('dispatch-worker').style.display = ''; $('fire-worker').style.display = '';
      await loadMessages('employee', selected.employee_id);
      document.querySelectorAll('.inspector-tab').forEach(tab => tab.classList.toggle('active', tab.dataset.tab === 'overview'));
      document.querySelectorAll('.inspector-body').forEach(body => body.classList.toggle('hidden', body.id !== 'detail-overview'));
    } catch (error) { window.alert(error.message); }
  }
  async function showOpenCodeSession(sessionId) {
    try {
      const snapshot = lastDashboard?.opencode_sessions?.sessions?.find(item => item.session_id === sessionId);
      if (!snapshot) return;
      selected = null; selectedSession = snapshot;
      $('inspector').classList.remove('hidden'); $('inspector').setAttribute('aria-hidden', 'false');
      $('detail-title').textContent = snapshot.title; $('detail-role').textContent = `${snapshot.agent || 'agent'} · ${snapshot.session_id}`;
      $('detail-avatar').textContent = 'O'; $('detail-status').textContent = snapshot.status || 'unknown';
      $('detail-state').innerHTML = `<h3>OpenCode session</h3><p><b>${esc(snapshot.status || 'unknown')}</b></p><p class="muted-line">Parent: ${esc(snapshot.parent_id || 'root')}<br>Directory: ${esc(snapshot.directory || '—')}</p>`;
      $('detail-evidence').innerHTML = `<h3>Runtime identity</h3><p>Agent: ${esc(snapshot.agent || '—')}<br>Session ID: ${esc(snapshot.session_id)}<br>Outcome: ${esc(snapshot.outcome || '—')}</p>`;
      $('detail-json').textContent = JSON.stringify(snapshot, null, 2); $('detail-log').textContent = 'OpenCode session messages are shown in Chat.';
      $('dispatch-worker').style.display = 'none'; $('fire-worker').style.display = 'none';
      await loadSessionMessages(sessionId);
      document.querySelectorAll('.inspector-tab').forEach(tab => tab.classList.toggle('active', tab.dataset.tab === 'chat'));
      document.querySelectorAll('.inspector-body').forEach(body => body.classList.toggle('hidden', body.id !== 'detail-chat'));
    } catch (error) { window.alert(error.message); }
  }
  async function switchSupervisor(id) { if (!id || id === currentSupervisor) return; currentSupervisor = id; history.replaceState(null, '', `?supervisor_id=${encodeURIComponent(id)}`); renderWorkspaces(); $('inspector').classList.add('hidden'); if (stream) stream.close(); $('connection').textContent = 'Loading workspace…'; await refresh(); connect(); }
  function connect() {
    if (!currentSupervisor) return;
    const query = new URLSearchParams({ supervisor_id: currentSupervisor });
    if (token) query.set('token', token);
    stream = new EventSource(`/api/stream?${query}`);
    stream.addEventListener('dashboard', event => { try { renderDashboard(JSON.parse(event.data)); api(`/api/events?supervisor_id=${encodeURIComponent(currentSupervisor)}`).then(value => renderActivity(value.events || [])).catch(() => {}); $('connection').innerHTML = '<i></i>Live'; $('connection').className = 'connection'; } catch (_) {} });
    stream.onerror = () => { $('connection').textContent = 'Reconnecting'; $('connection').className = 'connection error'; };
  }
  async function sendCommand(text) {
    const result = await api('/api/message', { method: 'POST', body: JSON.stringify({ supervisor_id: currentSupervisor, target_type: 'supervisor', target_id: currentSupervisor, text }) });
    $('command-result').textContent = `Sent to ${result.target_id}`; return result;
  }
  $('refresh').addEventListener('click', refresh);
  $('command-open').addEventListener('click', async () => { $('command-modal').classList.remove('hidden'); try { const value = await api(`/api/messages?supervisor_id=${encodeURIComponent(currentSupervisor)}&target_type=supervisor&target_id=${encodeURIComponent(currentSupervisor)}`); renderMessages(value.messages || [], 'supervisor-messages', 'No supervisor messages yet.'); } catch (error) { $('command-result').textContent = error.message; } });
  $('command-close').addEventListener('click', () => $('command-modal').classList.add('hidden'));
  $('close-detail').addEventListener('click', () => { $('inspector').classList.add('hidden'); $('inspector').setAttribute('aria-hidden', 'true'); });
  document.querySelector('.inspector-backdrop').addEventListener('click', () => $('inspector').classList.add('hidden'));
  $('command-form').addEventListener('submit', async event => { event.preventDefault(); const text = $('command-input').value.trim(); if (!text) return; try { await sendCommand(text); $('command-input').value = ''; } catch (error) { $('command-result').textContent = error.message; } });
  document.querySelectorAll('.view-switch').forEach(button => button.addEventListener('click', () => { viewMode = button.dataset.view; document.querySelectorAll('.view-switch').forEach(item => item.classList.toggle('active', item === button)); if (lastDashboard) renderDashboard(lastDashboard); }));
  $('filter-button').addEventListener('click', () => { filterMode = filterMode === 'all' ? 'attention' : filterMode === 'attention' ? 'active' : 'all'; $('filter-button').innerHTML = `Filter: ${filterMode[0].toUpperCase() + filterMode.slice(1)} <span>☷</span>`; if (lastDashboard) renderDashboard(lastDashboard); });
  $('activity-all').addEventListener('click', () => { activityExpanded = !activityExpanded; renderActivity(lastEvents); });
  $('employee-chat-form').addEventListener('submit', async event => { event.preventDefault(); const input = $('employee-chat-input'); const text = input.value.trim(); if (!text) return; try { if (selectedSession) { await api('/api/opencode-message', { method: 'POST', body: JSON.stringify({ supervisor_id: currentSupervisor, session_id: selectedSession.session_id, text }) }); input.value = ''; await loadSessionMessages(selectedSession.session_id); } else if (selected) { await api('/api/message', { method: 'POST', body: JSON.stringify({ supervisor_id: currentSupervisor, target_type: 'employee', target_id: selected.employee_id, text }) }); input.value = ''; await loadMessages('employee', selected.employee_id); } } catch (error) { window.alert(error.message); } });
  window.setInterval(() => {
    if (selected && !$('inspector').classList.contains('hidden')) loadMessages('employee', selected.employee_id).catch(() => {});
    if (!$('command-modal').classList.contains('hidden') && currentSupervisor) api(`/api/messages?supervisor_id=${encodeURIComponent(currentSupervisor)}&target_type=supervisor&target_id=${encodeURIComponent(currentSupervisor)}`).then(value => renderMessages(value.messages || [], 'supervisor-messages', 'No supervisor messages yet.')).catch(() => {});
  }, 3000);
  $('dispatch-worker').addEventListener('click', async () => { if (!selected) return; try { await api('/api/dispatch', { method: 'POST', body: JSON.stringify({ supervisor_id: currentSupervisor, employee_id: selected.employee_id }) }); await refresh(); await showInspector(selected.employee_id); } catch (error) { window.alert(error.message); } });
  $('fire-worker').addEventListener('click', async () => { if (!selected || !window.confirm(`Fire ${selected.employee_id} and its child tree?`)) return; const reason = window.prompt('Reason'); if (!reason) return; try { await api('/api/fire', { method: 'POST', body: JSON.stringify({ supervisor_id: currentSupervisor, employee_id: selected.employee_id, reason }) }); await refresh(); await showInspector(selected.employee_id); } catch (error) { window.alert(error.message); } });
  document.querySelectorAll('.inspector-tab').forEach(tab => tab.addEventListener('click', () => {
    document.querySelectorAll('.inspector-tab').forEach(item => item.classList.remove('active'));
    tab.classList.add('active');
    document.querySelectorAll('.inspector-body').forEach(body => {
      const visible = body.id === `detail-${tab.dataset.tab}` || (tab.dataset.tab === 'evidence' && body.id === 'detail-evidence-tab');
      body.classList.toggle('hidden', !visible);
    });
  }));
  $('theme-toggle').addEventListener('click', () => document.body.classList.toggle('dark-mode'));
  loadSupervisors().then(() => { refresh(); connect(); }).catch(error => { $('connection').textContent = error.message; $('connection').className = 'connection error'; });
})();
