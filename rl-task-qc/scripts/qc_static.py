#!/usr/bin/env python3
"""Static gate for RL task deliveries (Harbor task dirs or SForge task dirs).

Usage:
  python3 qc_static.py <delivery_root> [--format auto|sforge-only|harbor]
      [--profile profile.json]
      [--json out.json] [--third-party GLOB ...]

Checks only files; never builds or runs containers. Exit code 1 if any FAIL.
Rules carry a source tag: [V2.4] Meta Vendor Data Delivery Spec V2.4,
[SMOL] SmolData harbor-task-v2 preflight contract, [V1] EdgeBench Requirement V1,
[L:date] lesson learned on that date (see ../lessons.md).
"""
import argparse, hashlib, json, os, re, stat, sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # py<3.11
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

def mini_toml(text):
    """Fallback: sections + scalar/array/string keys only (enough for task.toml)."""
    cfg, cur = {}, None
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip() if not raw.strip().startswith('"') else raw.strip()
        if not line: continue
        m = re.match(r'^\[([^\]]+)\]$', line)
        if m:
            cur = cfg
            for part in m.group(1).split('.'):
                cur = cur.setdefault(part.strip(), {})
            continue
        m = re.match(r'^([A-Za-z0-9_.-]+)\s*=\s*(.*)$', line)
        if not m: continue
        k, v = m.group(1), m.group(2).strip()
        if v.startswith('"') and v.endswith('"'): v = v[1:-1]
        elif v.startswith('['): v = [x.strip().strip('"') for x in v.strip('[]').split(',') if x.strip()]
        elif v in ('true', 'false'): v = v == 'true'
        else:
            try: v = float(v) if '.' in v else int(v)
            except ValueError: pass
        (cur if cur is not None else cfg)[k] = v
    return cfg

FINDINGS = []  # (level, scope, rule, message, evidence)

def add(level, scope, rule, msg, ev=""):
    FINDINGS.append((level, scope, rule, msg, ev))

TEXT_EXT = {'.md', '.txt', '.py', '.sh', '.toml', '.json', '.yaml', '.yml', '.cfg', '.ini', '.csv', '.lean', '.Dockerfile', ''}
BIN_EXT = {'.tar', '.gz', '.tgz', '.zip', '.xz', '.whl', '.so', '.png', '.jpg', '.jpeg', '.wasm', '.olean', '.deb', '.bin', '.npz', '.pyc', '.oci', '.pdf'}
JUNK_RE = re.compile(r'(^|/)(\.DS_Store|__MACOSX|__pycache__|Thumbs\.db|\.idea|\.vscode)(/|$)|\.pyc$')
DEFAULT_THIRD_PARTY = ['*/workspace/*', '*/reference_data/*', '*/site-packages/*', '*/node_modules/*', '*/vendor/*', '*LICENSE*', '*/licenses/*',
                       '*/observations/*', '*/submissions/*', '*transcript*', '*agent_output*', '*/historical_replay/*', '*/trajectories/*']

# Leak patterns: (regex, label, severity)
LEAK = [
    (r'[\u4e00-\u9fff]', 'chinese', 'FAIL'),
    (r'[\u3010\u3011\uff1a\uff0c]', 'fullwidth punctuation', 'FAIL'),
    (r'\u2014|\u2013', 'em/en dash', 'FAIL'),
    (r'\u00b7', 'interpunct', 'WARN'),
    (r'/Users/[A-Za-z]|/home/[a-z]+/|/root/terminal_bench|C:\\\\Users', 'local path', 'FAIL'),
    (r'\b(mindforge|dmx|bytedance|alibaba|meituan|ant ?group)\b', 'vendor/other-client name', 'FAIL'),
    (r'\b(codex|claude code|kimi|cursor|copilot)\b', 'AI tool name', 'WARN'),
    (r'\b(quota|rate.?limit(ed)?|usage limit|interrupted|reboot|infrastructure_error|unscored|waive[d]?|excluded.by.owner|provisional|candidate status|COHORT_INELIGIBLE|sanitiz(ed|e)|redact(ed)?|checkpoint)\b', 'internal process/state word', 'WARN'),
    (r'\b(revision\s+\d|revised|corrected|repair(?:\d+|[-_]\d+)|stage ?[0-9]|v[2-9]\b|-v[0-9]+\b|under separate review|reviewer must)\b', 'revision history word', 'WARN'),
    (r'\bI (designed|assembled|built|wrote)\b', 'first person', 'WARN'),
    (r'host\.docker\.internal|\.local\b|gateway', 'host gateway / local endpoint', 'WARN'),
    (r'\b(sk-[A-Za-z0-9]{20,}|xox[bp]-[A-Za-z0-9-]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16})\b|api[_-]?key\s*[:=]\s*["\'][A-Za-z0-9]{16,}', 'credential-looking token', 'FAIL'),
]
LEAK_RE = [(re.compile(p, re.I), l, s) for p, l, s in LEAK]

def rel(p, root):
    return os.path.relpath(p, root)

def fnmatch_any(path, globs):
    from fnmatch import fnmatch
    return any(fnmatch(path, g) or fnmatch('/' + path, g) for g in globs)

def walk_files(root):
    for a, dirs, fs in os.walk(root):
        for f in fs:
            yield os.path.join(a, f)

def read_text(p, limit=5_000_000):
    try:
        if os.path.getsize(p) > limit: return None
        return Path(p).read_text(encoding='utf-8')
    except Exception:
        return None

# ---------- package-level ----------
def check_package(root, third_party):
    files = list(walk_files(root))
    rels = [rel(f, root) for f in files]
    # junk
    junk = [r for r in rels if JUNK_RE.search(r)]
    (add('FAIL', 'package', 'P1 junk files [PACK cleanup]', f'{len(junk)} junk files', ', '.join(junk[:5])) if junk else add('PASS', 'package', 'P1 junk files', 'none'))
    # non-ascii paths
    na = [r for r in rels if any(ord(c) > 127 for c in r)]
    (add('FAIL', 'package', 'P2 ASCII paths [PACK]', f'{len(na)} non-ASCII paths', ', '.join(na[:5])) if na else add('PASS', 'package', 'P2 ASCII paths', 'all ASCII'))
    # The outer customer folder may contain the documented brand, spaces and
    # date. Apply ASCII rules to package members, not the reviewer's extraction
    # directory name.
    add('PASS', 'package', 'P2b outer delivery name', 'outer directory is not a package-member rule')
    # permissions
    badperm = []
    for f, r in zip(files, rels):
        m = stat.S_IMODE(os.stat(f).st_mode)
        want = 0o755 if r.endswith('.sh') else 0o644
        if m != want: badperm.append(f'{r}={oct(m)[-3:]}')
    (add('FAIL', 'package', 'P3 file modes [SMOL]', f'{len(badperm)} files not 0644 / .sh 0755', ', '.join(badperm[:6])) if badperm else add('PASS', 'package', 'P3 file modes', '0644 and .sh 0755'))
    # symlinks
    links = [rel(os.path.join(a, x), root) for a, ds, fs in os.walk(root) for x in ds + fs if os.path.islink(os.path.join(a, x))]
    (add('FAIL', 'package', 'P4 symlinks [L:2026-09-25]', f'{len(links)} symlinks', ', '.join(links[:5])) if links else add('PASS', 'package', 'P4 symlinks', 'none'))
    # SHA manifest
    man = [f for f in files if os.path.basename(f) in ('SHA256SUMS', 'SHA256SUMS.txt', 'sha256.json')]
    if not man:
        add('FAIL', 'package', 'P5 SHA manifest [V1]', 'no SHA256SUMS or sha256.json anywhere in package')
    else:
        for m in man:
            base = os.path.dirname(m)
            # manifests under <task>/metadata/ are relative to <task>
            if os.path.basename(base) == 'metadata': base = os.path.dirname(base)
            entries = {}
            try:
                if m.endswith('.json'):
                    d = json.load(open(m)); d = d.get('files', d)
                    entries = {k: (v if isinstance(v, str) else v.get('sha256')) for k, v in d.items()}
                else:
                    for line in open(m, encoding='utf-8'):
                        line = line.rstrip('\n')
                        if not line.strip(): continue
                        h, p = line.split(None, 1); entries[p.lstrip('*')] = h
            except Exception as e:
                add('FAIL', 'package', 'P5 SHA manifest', f'{rel(m, root)} unparsable: {e}'); continue
            bad = []; missing = []
            for p, h in entries.items():
                fp = os.path.join(base, p)
                if not os.path.exists(fp): missing.append(p); continue
                if hashlib.sha256(open(fp, 'rb').read()).hexdigest() != h: bad.append(p)
            covered = set(entries)
            unlisted = [rel(f, base) for f in walk_files(base) if rel(f, base) not in covered and f != m]
            lvl = 'FAIL' if (bad or missing) else ('WARN' if unlisted else 'PASS')
            add(lvl, 'package', 'P5 SHA manifest [V1]', f'{rel(m, root)}: {len(entries)} listed, {len(bad)} mismatch, {len(missing)} missing, {len(unlisted)} unlisted',
                ', '.join((bad + missing + unlisted)[:5]))
    # leak scan
    hits = {}
    for f, r in zip(files, rels):
        if fnmatch_any(r, third_party): continue
        ext = os.path.splitext(f)[1].lower()
        if ext in BIN_EXT: continue
        t = read_text(f)
        if t is None: continue
        prose = ext in ('.md', '.txt', '.json', '.csv', '.toml', '.yaml', '.yml', '')
        for rx, label, sev in LEAK_RE:
            if label in ('em/en dash', 'interpunct', 'first person', 'revision history word', 'internal process/state word', 'AI tool name') and not prose: continue
            m = rx.search(t)
            if m:
                line = t.count('\n', 0, m.start()) + 1
                hits.setdefault((label, sev), []).append(f'{r}:{line}')
    for (label, sev), locs in sorted(hits.items()):
        add(sev, 'package', f'P6 leak: {label} [PACK/PQC]', f'{len(locs)} files', ', '.join(locs[:4]))
    if not hits: add('PASS', 'package', 'P6 leak scan', 'clean (third-party dirs excluded)')
    # internal QA / rubric files shipped
    qa = [r for r in rels if re.search(r'(requirement-audit|pre-rollout|post_rollout|task_quality|quality_report|qc_report|rubric|审|质检)', r, re.I)]
    if qa: add('WARN', 'package', 'P7 internal QA files [L:2026-09-24]', f'{len(qa)} QA/rubric files in client package', ', '.join(qa[:5]))
    # root-level non-task dirs
    return files

# ---------- task discovery ----------
def find_tasks(root, maxdepth=5):
    harbor, sforge = [], []
    for a, dirs, fs in os.walk(root):
        depth = rel(a, root).count(os.sep)
        if depth > maxdepth: dirs[:] = []; continue
        if any(x in a for x in ('/observations', '/submissions', '/historical_replay', '/trajectories', '/site-packages')): dirs[:] = []; continue
        if 'task.toml' in fs: harbor.append(a)
        for f in fs:
            if f.endswith('.json') and not f.startswith('.'):
                try:
                    d = json.load(open(os.path.join(a, f)))
                    if isinstance(d, dict) and 'task_id' in d and 'work' in d and 'judge' in d:
                        if os.path.basename(a) in ('arm64', 'amd64', 'metadata', 'platforms') or '/platforms/' in a or '/metadata/' in a: continue
                        sforge.append((a, os.path.join(a, f)))
                except Exception: pass
    return harbor, sforge

def big_ints(text):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in re.finditer(r'=\s*(-?\d{16,})\s*(#.*)?$', line.strip()):
            if abs(int(m.group(1))) > 2**53: out.append(f'line {i}')
    return out

# ---------- Dockerfile ----------
def check_dockerfile(path, scope, tag):
    t = read_text(path)
    if t is None: add('FAIL', scope, f'D0 {tag} Dockerfile', 'missing or unreadable', path); return
    lines = [l.strip() for l in t.splitlines()]
    froms = [i for i, l in enumerate(lines) if l.upper().startswith('FROM ')]
    if not froms: add('FAIL', scope, f'D1 {tag} FROM', 'no FROM'); return
    last_stage = lines[froms[-1]:]
    users = [l for l in last_stage if l.upper().startswith('USER ')]
    final = users[-1].split(None, 1)[1].strip() if users else None
    if final is None:
        add('WARN', scope, f'D2 {tag} final USER [SMOL][L:2026-10-02]', 'no USER in final stage: base image default user must be root (docker image inspect --format {{.Config.User}})')
    elif final not in ('root', '0'):
        add('FAIL', scope, f'D2 {tag} final USER [SMOL][L:2026-10-02]', f'final stage ends as USER {final}; must end USER root (set agent user in task.toml [agent].user)')
    else:
        add('PASS', scope, f'D2 {tag} final USER', 'USER root')
    base = lines[froms[0]].split()[1]
    base_l = base.lower()
    if '${' in base:  # ARG indirection: resolve
        m = re.search(r'ARG\s+' + re.escape(base.strip('${}')) + r'\s*=\s*(\S+)', t)
        base = m.group(1) if m else base; base_l = base.lower()
    if '@sha256:' not in base_l and not base_l.startswith('${'):
        add('WARN', scope, f'D3 {tag} base pin [V2.4 S2]', f'base image not digest-pinned: {base}')
    if re.match(r'^[a-z0-9_-]+/[a-z0-9_.-]+:sha256-', base_l) or ('edgebench-tools' in base_l) or re.match(r'^(localhost|127\.0\.0\.1)', base_l):
        add('FAIL', scope, f'D4 {tag} base reachable [L:2026-10-05]', f'base image looks local/private, platform cannot pull: {base}')
    if re.search(r'--platform=linux/(amd64|arm64)', t):
        add('FAIL', scope, f'D5 {tag} arch pin [V2.4 S2]', 'Dockerfile hardcodes --platform; must build for amd64 and arm64')
    if re.search(r'NOPASSWD', t):
        add('FAIL', scope, f'D6 {tag} sudo [L:2026-10-06]', 'passwordless sudo granted inside image')
    if re.search(r'apt-get install', t) and not re.search(r'snapshot\.debian|apt-lock|install_locked|=[0-9]', t):
        add('WARN', scope, f'D7 {tag} apt pin [V1 §6]', 'apt-get install without version pins or snapshot')
    if tag == 'verifier' and re.search(r'chmod\s+(-R\s+)?0?700\s+/tests\b', t):
        add('WARN', scope, f'D8 {tag} /tests 700 [L:2026-10-01]', 'chmod 700 /tests at build time can break customer sandbox')
    users_any = re.findall(r'^USER\s+(\S+)', t, re.M)
    return final

# ---------- verifier source ----------
def check_verifier_sources(dirpath, scope):
    pys = [f for f in walk_files(dirpath) if f.endswith(('.py', '.sh'))]
    for f in pys:
        t = read_text(f)
        if not t: continue
        r = os.path.basename(dirpath) + '/' + rel(f, dirpath)
        if re.search(r'setrlimit', t) and not re.search(r'getrlimit', t):
            add('FAIL', scope, 'V1 rlimit [L:2026-10-01]', 'setrlimit without getrlimit clamp (sandbox hard limits are lower, no CAP_SYS_RESOURCE)', r)
        if re.search(r'\b(65534|65532)\b', t) and re.search(r'setuid|setgid|--user|runuser|su ', t):
            add('WARN', scope, 'V2 shared uid [L:2026-10-01]', 'drops to uid 65534/65532 (NPROC is per-uid host-wide); use a unique high uid', r)
        if re.search(r'os\.setuid|os\.setgid|os\.chown|shutil\.chown', t) and not re.search(r'geteuid\(\)\s*(==|!=)\s*0', t):
            add('WARN', scope, 'V3 privilege guard [L:2026-10-01]', 'setuid/chown without geteuid()==0 guard', r)
        if re.search(r'\.extractall\(', t) and not re.search(r'filter\s*=', t):
            add('WARN', scope, 'V4 tar extract [L:2026-09-25]', 'extractall without filter= (symlink/path traversal)', r)
        if re.search(r'copytree\(', t) and not re.search(r'symlinks\s*=\s*False', t):
            add('WARN', scope, 'V4 symlink follow [L:2026-09-25]', 'copytree may follow symlinks into private data', r)
        mm = re.search(r'^[^#\n]*(chmod\s+(-R\s+)?0?777|\.chmod\(0o1?777|os\.chmod\([^)]*0o1?777)', t, re.M)
        if mm:
            add('WARN', scope, 'V5 world-writable [L:2026-09-25]', 'world-writable path in verifier: confirm candidate cannot reach the scoring output', f'{r}:{t.count(chr(10), 0, mm.start()) + 1}')
        if re.search(r'import (numpy|scipy|torch)|from (numpy|scipy)', t) and not re.search(r'OPENBLAS_NUM_THREADS|OMP_NUM_THREADS', t):
            add('WARN', scope, 'V6 BLAS threads [L:2026-10-01]', 'numpy/scipy without OPENBLAS/OMP thread pin (thread count = host cores, hits NPROC)', r)
        if f.endswith('test.sh') and 'set -e' in t and 'reward.txt' in t and not re.search(r'trap|\|\|', t):
            add('WARN', scope, 'V7 reward on error [L:2026-10-01]', 'set -e with no trap: an early failure skips writing reward.txt', r)
        if re.search(r'subprocess\.(Popen|run)', t) and re.search(r'kill\(|killpg|SIGKILL', t) and not re.search(r'wait\(|waitpid|State:\s*Z|--init|start_new_session', t):
            add('WARN', scope, 'V8 zombie reap [L:2026-09-14]', 'kills children without wait/reap; zombie under non-reaping PID 1 breaks uid sweeps', r)
    # answers shipped where the candidate can read them
    for f in walk_files(dirpath):
        r = rel(f, dirpath)
        if re.search(r'(^|/)(solution|solutions|reference|references|oracle|strong|calibration|golden|answers?)[^/]*', r, re.I) and '/private/' not in '/' + r and not r.endswith('.md'):
            add('WARN', scope, 'V9 answer in judge context [L:2026-10-02]', 'reference/calibration file inside judge build context outside a root-only private dir', os.path.basename(dirpath) + '/' + r)

# ---------- Harbor task ----------
def check_harbor(tdir, root):
    name = os.path.basename(tdir)
    if name in ('task', 'tasks'):
        parts = rel(tdir, root).split(os.sep); name = next((p for p in reversed(parts) if p not in ('task', 'tasks')), name)
    scope = name
    toml_p = os.path.join(tdir, 'task.toml'); t = read_text(toml_p) or ''
    if not re.fullmatch(r'[a-z0-9]+(?:[_-][a-z0-9]+)*', name):
        add('FAIL', scope, 'H1 dir name [Meta V3.0]', f'"{name}" is not a lowercase snake_case or kebab-case task name')
    bi = big_ints(t)
    if bi: add('FAIL', scope, 'H2 TOML big int [L:2026-10-05]', 'integer > 2^53 breaks platform TOML parser; quote it', ', '.join(bi))
    cfg = {}
    try: cfg = tomllib.loads(t) if tomllib else mini_toml(t)
    except Exception as e: add('FAIL', scope, 'H2 TOML parse [SMOL]', str(e)[:120]); cfg = mini_toml(t)
    sidecar = not os.path.exists(os.path.join(tdir, 'tests')) and not os.path.exists(os.path.join(tdir, 'environment'))
    if sidecar:
        add('WARN', scope, 'H0 task.toml sidecar', 'task.toml next to an SForge task without Harbor layout: not uploadable to SmolData as-is')
        # still validate the fields it declares
        for sc in ('environment', 'agent', 'verifier'):
            nm = (cfg.get(sc) or {}).get('network_mode')
            if nm and sc in ('agent', 'verifier') and nm != 'no-network': add('WARN', scope, f'H4 [{sc}].network_mode [V1]', f'{nm}')
        return
    tn = (cfg.get('task') or {}).get('name') or cfg.get('name')
    if not tn: add('FAIL', scope, 'H3 [task].name [SMOL]', 'missing')
    elif tn.split('/')[-1] != name: add('FAIL', scope, 'H3 [task].name [V2.4 S3]', f'{tn} != dir {name}')
    elif '/' not in tn: add('WARN', scope, 'H3 [task].name [SMOL]', f'"{tn}" has no org/ prefix (accepted with warning)')
    else: add('PASS', scope, 'H3 [task].name', tn)
    for sc in ('environment', 'agent', 'verifier'):
        nm = (cfg.get(sc) or {}).get('network_mode')
        if nm not in ('public', 'no-network', 'allowlist'):
            add('FAIL', scope, f'H4 [{sc}].network_mode [V2.4 S4][SMOL]', f'missing or invalid: {nm}')
        elif sc in ('agent', 'verifier') and nm != 'no-network':
            add('WARN', scope, f'H4 [{sc}].network_mode [V1][L:2026-10-02]', f'{nm}: agent/verifier should be no-network; allowlist needs justification and host list')
        ah = (cfg.get(sc) or {}).get('allowed_hosts')
        if ah and nm != 'allowlist': add('FAIL', scope, f'H4 [{sc}].allowed_hosts [SMOL]', 'allowed_hosts only valid with allowlist')
    rt = cfg.get('reward_type') or (cfg.get('metadata') or {}).get('reward_type')
    if rt not in ('binary', 'bounded_continuous', 'unbounded_continuous'):
        add('FAIL', scope, 'H5 reward_type [V2.4 S5]', f'missing/invalid: {rt}')
    if not (cfg.get('agent') or {}).get('user'):
        add('WARN', scope, 'H6 [agent].user [L:2026-10-02]', 'no [agent].user; agent identity should be declared here, image ends USER root')
    for req in ('instruction.md', 'tests/test.sh', 'solution/solve.sh'):
        p = os.path.join(tdir, req)
        if not os.path.exists(p): add('FAIL', scope, f'H7 {req} [SMOL]', 'missing')
    envd = os.path.join(tdir, 'environment', 'Dockerfile')
    if os.path.exists(envd): check_dockerfile(envd, scope, 'environment')
    elif not any(os.path.exists(os.path.join(tdir, 'environment', x)) for x in ('docker-compose.yml', 'docker-compose.yaml')) and not (cfg.get('environment') or {}).get('docker_image'):
        add('FAIL', scope, 'H7 environment/Dockerfile [SMOL]', 'missing')
    td = os.path.join(tdir, 'tests', 'Dockerfile')
    if os.path.exists(td): check_dockerfile(td, scope, 'verifier')
    ts = os.path.join(tdir, 'tests', 'test.sh')
    if os.path.exists(ts):
        tt = read_text(ts) or ''
        if not tt.startswith('#!'): add('FAIL', scope, 'H8 test.sh shebang [SMOL]', 'no shebang')
        if 'reward.txt' not in tt: add('FAIL', scope, 'H8 test.sh reward [SMOL]', 'does not write /logs/verifier/reward.txt (must, including on failure)')
        if not os.access(ts, os.X_OK): add('FAIL', scope, 'H8 test.sh exec [SMOL]', 'not executable')
    ins = read_text(os.path.join(tdir, 'instruction.md')) or ''
    if re.search(r'\b(offline|no internet|without network|no network)\b', ins, re.I) and (cfg.get('agent') or {}).get('network_mode') == 'public':
        add('FAIL', scope, 'H9 instruction vs config [L:2026-09-25]', 'instruction says offline but [agent] is public')
    if os.path.isdir(os.path.join(tdir, 'tests')): check_verifier_sources(os.path.join(tdir, 'tests'), scope)
    # answers inside environment (agent image)
    for f in walk_files(os.path.join(tdir, 'environment')):
        r = rel(f, tdir)
        if re.search(r'(^|/)(solution|solutions|reference|references|golden|answers?|best_submission|strong)([._-][^/]*)?(/|$)', r, re.I) and not r.endswith('.md'):
            add('FAIL', scope, 'H10 answer in agent image [V2.4 Q5]', 'solution-like file inside environment/', r)

# ---------- SForge task ----------
def check_sforge(tdir, jpath, root):
    d = json.load(open(jpath)); tid = str(d.get('task_id'))
    scope = re.sub(r'_[0-9a-f]{8,}$', '', tid)
    # task dir should be named after task_id somewhere on the path
    if scope not in tdir.split(os.sep): add('WARN', scope, 'S1 task dir name', f'no directory named {scope} on path {rel(tdir, root)}')
    name = scope
    if not re.fullmatch(r'[a-z0-9]+(?:[_-][a-z0-9]+)*', scope): add('FAIL', scope, 'S1 task_id [Meta V3.0]', f'{tid} is not lowercase snake_case or kebab-case')
    if d.get('internet') not in (False, None): add('FAIL', scope, 'S2 internet [V1]', f'internet={d.get("internet")}')
    meta = d.get('metadata') or {}
    npol = meta.get('network_policy') or meta.get('network')
    troot = tdir
    while os.path.basename(troot) in ('tasks', 'task') and os.path.dirname(troot) != root: troot = os.path.dirname(troot)
    toml_pol = None
    for cand in (os.path.join(tdir, 'metadata', 'network-policy.toml'), os.path.join(troot, 'metadata', 'network-policy.toml'), os.path.join(tdir, 'task.toml'), os.path.join(troot, 'task.toml'), os.path.join(troot, 'task', 'task.toml')):
        if os.path.exists(cand): toml_pol = cand; break
    if not npol and not toml_pol: add('FAIL', scope, 'S3 three-scope network [V2.4 S4]', 'no environment/agent/verifier network declaration in JSON metadata or network-policy.toml')
    else:
        txt = json.dumps(npol) if npol else (read_text(toml_pol) or '')
        if 'allowlist' in txt or 'host.docker.internal' in txt: add('WARN', scope, 'S3 agent network [L:2026-10-03]', 'allowlist/gateway in declaration; client confirmed fully offline agent', toml_pol or jpath)
    rt = meta.get('reward_type') or (meta.get('scoring') or {}).get('reward_type')
    if rt not in ('binary', 'bounded_continuous', 'unbounded_continuous'): add('FAIL', scope, 'S4 reward_type [V2.4 S5]', f'missing/invalid: {rt}')
    if toml_pol and toml_pol.endswith('task.toml'):
        bi = big_ints(read_text(toml_pol) or '')
        if bi: add('FAIL', scope, 'S5 TOML big int [L:2026-10-05]', 'integer > 2^53 breaks platform parser; quote it', ', '.join(bi))
    # contexts
    ctx = None; roles = {'work': 'work', 'judge': 'judge'}
    for cand in (os.path.join(tdir, 'contexts'), os.path.join(troot, 'contexts')):
        if os.path.isdir(cand): ctx = cand; break
    if not ctx and os.path.isdir(os.path.join(troot, 'task', 'tests')):
        ctx = os.path.join(troot, 'task'); roles = {'work': 'environment', 'judge': 'tests'}
        add('WARN', scope, 'S6 contexts', 'SForge task builds from Harbor-style task/environment and task/tests')
    if not ctx: add('FAIL', scope, 'S6 contexts [V1 §6]', 'no contexts/work and contexts/judge build contexts'); return
    for role in ('work', 'judge'):
        df = os.path.join(ctx, roles[role], 'Dockerfile')
        if os.path.exists(df): check_dockerfile(df, scope, 'environment' if role == 'work' else 'verifier')
        else: add('FAIL', scope, f'S6 contexts/{role}/Dockerfile [V1 §6]', 'missing')
    # judge entry exists
    cmd = (d.get('judge') or {}).get('eval_cmd', '')
    m = re.search(r'(/tests/\S+\.(py|sh))', cmd)
    if m:
        target = m.group(1); fn = os.path.basename(target)
        found = any(os.path.basename(f) == fn for f in walk_files(os.path.join(ctx, roles['judge'])))
        if not found: add('FAIL', scope, 'S7 judge entry [L:2026-10-06]', f'eval_cmd calls {target} but no {fn} in judge build context (judge built from shipped source cannot run)')
    check_verifier_sources(os.path.join(ctx, roles['judge']), scope)
    # answers in work
    for f in walk_files(os.path.join(ctx, roles['work'])):
        r = rel(f, ctx)
        if re.search(r'(^|/)(solution|solutions|reference|references|golden|answers?|best_submission|strong)([._-][^/]*)?(/|$)', r, re.I) and not r.endswith('.md'):
            add('FAIL', scope, 'S8 answer in work image [V2.4 Q5]', 'solution-like file inside contexts/work', r)
    # NOP + reference evidence present
    nop = any('nop' in rel(f, troot).lower() for f in walk_files(troot))
    if not nop: add('WARN', scope, 'S9 NOP evidence [V1]', 'no nop/ directory or NOP result found')
    # instruction says offline?
    ins = (d.get('work') or {}).get('agent_query', '')
    if re.search(r'under separate review|reviewer must|repair candidate|historical normalization', ins, re.I):
        add('FAIL', scope, 'S10 prompt hygiene [L:2026-10-06]', 'agent prompt contains internal review/process text')

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root'); ap.add_argument('--json'); ap.add_argument('--third-party', nargs='*', default=[])
    ap.add_argument('--format', choices=('auto', 'sforge-only', 'harbor'), default='auto')
    ap.add_argument('--profile', help='validated Benchmark profile JSON used for scope labeling')
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    profile_id = 'unspecified'
    if a.profile:
        try:
            profile = json.load(open(a.profile, encoding='utf-8'))
            profile_id = str(profile.get('profile_id', 'custom'))
        except (OSError, ValueError, TypeError) as exc:
            add('FAIL', 'package', 'T-profile', f'cannot read profile: {exc}')
    third = DEFAULT_THIRD_PARTY + a.third_party
    sys.dont_write_bytecode = True
    check_package(root, third)
    harbor, sforge = find_tasks(root)
    selected = a.format
    if selected == 'auto':
        if harbor and not sforge:
            selected = 'harbor'
        elif sforge and not harbor:
            selected = 'sforge-only'
        elif harbor and sforge:
            selected = 'mixed'
    if selected == 'harbor':
        for t in harbor: check_harbor(t, root)
        if sforge:
            add('FAIL', 'package', 'T0 format', 'Harbor selection contains SForge task definitions; choose one delivery format')
    elif selected == 'sforge-only':
        for t, j in sforge: check_sforge(t, j, root)
        if harbor:
            add('FAIL', 'package', 'T0 format', 'SForge-only selection contains Harbor task.toml files; choose one delivery format')
    elif selected == 'mixed':
        add('FAIL', 'package', 'T0 format', 'mixed Harbor and SForge task definitions require an explicit single-format package')
    else:
        add('FAIL', 'package', 'T0 tasks', 'no Harbor task.toml or SForge task json found')
    order = {'FAIL': 0, 'WARN': 1, 'PASS': 2}
    seen = set(); uniq = []
    for f in FINDINGS:
        if f not in seen: seen.add(f); uniq.append(f)
    FINDINGS[:] = uniq
    FINDINGS.sort(key=lambda x: (order[x[0]], x[1], x[2]))
    nf = sum(1 for x in FINDINGS if x[0] == 'FAIL'); nw = sum(1 for x in FINDINGS if x[0] == 'WARN')
    print(f'# Static gate: {root}\n')
    print(f'Profile: {profile_id}. Format: {selected}. Tasks: {len(harbor)} Harbor, {len(sforge)} SForge. **{nf} FAIL, {nw} WARN.**\n')
    print('| Level | Scope | Rule | Finding | Evidence |\n|---|---|---|---|---|')
    for lvl, sc, rule, msg, ev in FINDINGS:
        print(f'| {lvl} | {sc} | {rule} | {msg} | {ev[:160]} |')
    print('\nLegend: [V3] Meta Vendor Data Spec V3.0, [V1] EdgeBench Vendor Dataset Requirements V1, [PQC]/[PACK] stage documents, [L:date] lessons. Static only: Oracle/NOP, dual-arch, ulimit and variants need the runtime gate.')
    if a.json: json.dump([dict(level=l, scope=s, rule=r, finding=m, evidence=e) for l, s, r, m, e in FINDINGS], open(a.json, 'w'), indent=1, ensure_ascii=False)
    sys.exit(1 if nf else 0)

if __name__ == '__main__':
    main()
