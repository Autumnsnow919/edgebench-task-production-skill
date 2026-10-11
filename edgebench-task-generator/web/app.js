(() => {
  const params = new URLSearchParams(location.search);
  const token = params.get('token') || localStorage.getItem('edgebench-console-token');
  if (token) localStorage.setItem('edgebench-console-token', token);
  const headers = () => token ? { Authorization: `Bearer ${token}` } : {};
  let dashboard = null;
  let selected = null;

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = (value) => value ? new Date(value).toLocaleString() : '—';
  const api = async (path, options = {}) => {
    const response = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}), 'Content-Type': 'application/json' } });
    const value = await response.json().catch(() => ({ error: response.statusText }));
    if (!response.ok) throw new Error(value.error || response.statusText);
    return value;
  };

  function statusClass(status) {
    return ['active','running','rollout-running','needs-owner-decision','replacement-required','repair-required','fired'].includes(status) ? status : '';
  }
  function render(value) {
    dashboard = value;
    $('active').textContent = value.capacity?.active ?? '—';
    $('capacity').textContent = `${value.capacity?.available ?? 0} / ${value.capacity?.max_concurrent ?? 0}`;
    $('alert-count').textContent = value.alerts?.length ?? 0;
    $('updated').textContent = new Date().toLocaleTimeString();
    $('mode').textContent = `${value.employees?.length ?? 0} workers · live stream`;
    $('alerts').innerHTML = (value.alerts || []).map(alert => `<div class="alert ${String(alert.status).includes('FAIL') || alert.status === 'EARLY_TOO_EASY' ? 'danger' : ''}"><strong>${esc(alert.kind)}</strong> · ${esc(alert.employee_id)} · ${esc(alert.status)} ${alert.action ? `· ${esc(alert.action)}` : ''}</div>`).join('');
    $('employees').innerHTML = (value.employees || []).map(employee => {
      const report = employee.report || {};
      const gate = employee.rollout_gate || {};
      return `<tr>
        <td><strong>${esc(employee.employee_id)}</strong><small>${esc(employee.parent_id || 'supervisor')}</small></td>
        <td>${esc(employee.role)}<small>${esc(employee.transport)}</small></td>
        <td><strong>${esc(employee.task_id || 'unassigned')}</strong><small>${esc(employee.seed_id || '')}</small></td>
        <td><span class="status ${statusClass(employee.status)}">${esc(employee.status)}</span></td>
        <td><strong>${esc(report.status || '—')}</strong><small>${esc(report.summary || fmt(report.recorded_at))}</small></td>
        <td><strong>${esc(gate.status || '—')}</strong><small>${esc(gate.action || '')}</small></td>
        <td><button class="button secondary view-worker" data-id="${esc(employee.employee_id)}">View</button></td>
      </tr>`;
    }).join('') || '<tr><td colspan="7" class="empty">No employees.</td></tr>';
    document.querySelectorAll('.view-worker').forEach(button => button.addEventListener('click', () => showEmployee(button.dataset.id)));
  }
  async function refresh() {
    try { render(await api('/api/dashboard')); $('connection').textContent = 'live'; $('connection').className = 'pill good'; }
    catch (error) { $('connection').textContent = error.message; $('connection').className = 'pill bad'; }
  }
  async function showEmployee(id) {
    try {
      const value = await api(`/api/dashboard?employee_id=${encodeURIComponent(id)}`);
      selected = value.employees?.[0];
      if (!selected) return;
      $('detail').classList.remove('hidden'); $('detail-title').textContent = id;
      $('detail-state').textContent = JSON.stringify({ status: selected.status, role: selected.role, parent_id: selected.parent_id, task_id: selected.task_id, seed_id: selected.seed_id, last_dispatch_id: selected.last_dispatch_id }, null, 2);
      $('detail-evidence').textContent = JSON.stringify({ report: selected.report, qc: selected.qc, rollout_gate: selected.rollout_gate }, null, 2);
    } catch (error) { alert(error.message); }
  }
  $('refresh').addEventListener('click', refresh);
  $('close-detail').addEventListener('click', () => $('detail').classList.add('hidden'));
  $('command-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = $('command-input'); if (!input.value.trim()) return;
    try { const result = await api('/api/command', { method: 'POST', body: JSON.stringify({ text: input.value }) }); $('command-result').textContent = `Queued: ${result.command_sha256}`; input.value = ''; }
    catch (error) { $('command-result').textContent = error.message; }
  });
  $('dispatch-worker').addEventListener('click', async () => { if (!selected) return; try { await api('/api/dispatch', { method: 'POST', body: JSON.stringify({ employee_id: selected.employee_id }) }); await showEmployee(selected.employee_id); await refresh(); } catch (error) { alert(error.message); } });
  $('fire-worker').addEventListener('click', async () => { if (!selected || !confirm(`Fire ${selected.employee_id} and its child tree?`)) return; const reason = prompt('Reason'); if (!reason) return; try { await api('/api/fire', { method: 'POST', body: JSON.stringify({ employee_id: selected.employee_id, reason }) }); await refresh(); await showEmployee(selected.employee_id); } catch (error) { alert(error.message); } });
  function connect() {
    if (!token) { $('connection').textContent = 'missing token'; $('connection').className = 'pill bad'; return; }
    const stream = new EventSource(`/api/stream?token=${encodeURIComponent(token)}`);
    stream.addEventListener('dashboard', event => { try { render(JSON.parse(event.data)); $('connection').textContent = 'live'; $('connection').className = 'pill good'; } catch (_) {} });
    stream.onerror = () => { $('connection').textContent = 'reconnecting'; $('connection').className = 'pill warn'; };
  }
  refresh(); connect();
})();
