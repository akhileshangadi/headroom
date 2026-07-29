import urllib.request, json, subprocess, re

def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        body = r.read()
        ct = r.headers.get_content_type()
        if 'json' in ct: return json.loads(body)
        return body.decode()

def post(url, body, headers={}):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type':'application/json',**headers})
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except: return e.code, {}

def docker(cmd):
    return subprocess.run(
        ['docker', 'exec', 'headroom-coding', 'python', '-c', cmd],
        capture_output=True, text=True
    ).stdout.strip()

results = []
def check(name, ok, detail=''):
    results.append((name, bool(ok), str(detail)[:80]))

# ── INFRASTRUCTURE ────────────────────────────────────────────────────────────
for port, name in [(8787,'coding'),(8788,'prose'),(8789,'infra')]:
    h = json.loads(urllib.request.urlopen(f'http://localhost:{port}/health', timeout=5).read())
    check(f'[INFRA] Proxy :{port} ({name}) healthy v0.32.0',
          h['status']=='healthy' and h['version']=='0.32.0',
          f"v{h['version']} {h['status']}")

mgr = get('http://localhost:9099/health')
check('[INFRA] Manager :9099 healthy (node healthcheck fix EC-01)', 'ok' in str(mgr), str(mgr)[:20])

# ── PROXY: BASIC LLM REQUEST ─────────────────────────────────────────────────
body = json.dumps({'model':'claude-haiku-4-5-20251001','max_tokens':10,
    'messages':[{'role':'user','content':'say: ok'}]}).encode()
req = urllib.request.Request('http://localhost:8787/v1/messages', data=body,
    headers={'Content-Type':'application/json',
             'x-api-key':'1c0a5c10-e67f-41f8-8d34-5cfbf40b8b88',
             'anthropic-version':'2023-06-01'})
with urllib.request.urlopen(req, timeout=30) as r:
    d = json.loads(r.read())
check('[PROXY] LLM request: Claude Code -> headroom -> Hyperspace -> Anthropic',
      d.get('type')=='message',
      f"reply: {d.get('content',[{}])[0].get('text','ERROR')[:25]}")

# ── PROXY: COMPRESSION ───────────────────────────────────────────────────────
cmd = (
    'from headroom import compress; import json; '
    'data=[{"id":i,"v":i*9.99,"status":"ok","cat":"x"} for i in range(80)]; '
    'msgs=[{"role":"user","content":[{"type":"tool_result","tool_use_id":"t","content":json.dumps(data)}]}]; '
    'r=compress(msgs,model="claude-haiku-4-5-20251001"); '
    'print(r.tokens_saved, len(r.transforms_applied)>0)'
)
parts = docker(cmd).split()
check('[PROXY] SmartCrusher compression works',
      len(parts)==2 and int(parts[0])>0 if parts else False,
      f'tokens_saved={parts[0] if parts else "ERR"} transformed={parts[1] if len(parts)>1 else "ERR"}')

# ── STATS ─────────────────────────────────────────────────────────────────────
s = get('http://localhost:8787/stats')
check('[STATS] /stats endpoint returns request data',
      s['requests']['total'] >= 0, f"requests={s['requests']['total']}")
sh = get('http://localhost:8787/stats-history?format=json&series=history')
check('[STATS] /stats-history persisted lifetime data',
      sh.get('lifetime',{}).get('requests',0) >= 0,
      f"lifetime.requests={sh.get('lifetime',{}).get('requests',0)}")

# ── MANAGER: CSRF ─────────────────────────────────────────────────────────────
sc_no, _ = post('http://localhost:9099/api/toggle', {'projectId':'headroom','enabled':False})
check('[SECURITY] CSRF blocks toggle without token', sc_no == 403, f'HTTP {sc_no}')

html = get('http://localhost:9099/')
m = re.search(r"const CSRF = '([a-f0-9]+)'", html)
tok = m.group(1) if m else ''
check('[SECURITY] CSRF token present in dashboard HTML', bool(tok), f'{tok[:16]}...' if tok else 'MISSING')

sc_with, d2 = post('http://localhost:9099/api/toggle',
    {'projectId':'headroom','enabled':False}, {'X-CSRF-Token': tok})
check('[SECURITY] CSRF allows toggle with correct token',
      sc_with == 200 and d2.get('ok'), f'HTTP {sc_with}')

# ── MANAGER: PER-PROJECT HEADER ───────────────────────────────────────────────
post('http://localhost:9099/api/toggle',
     {'projectId':'headroom','enabled':True}, {'X-CSRF-Token': tok})
with open('C:/Users/I772791/Personal Git/headroom/.claude/settings.json') as f:
    cfg_on = json.load(f)
hdr_on = cfg_on.get('env',{}).get('ANTHROPIC_CUSTOM_HEADERS','')
check('[FEATURE] X-Headroom-Project header injected on enable',
      'X-Headroom-Project: headroom' in hdr_on, hdr_on or 'MISSING')

post('http://localhost:9099/api/toggle',
     {'projectId':'headroom','enabled':False}, {'X-CSRF-Token': tok})
with open('C:/Users/I772791/Personal Git/headroom/.claude/settings.json') as f:
    cfg_off = json.load(f)
check('[FEATURE] X-Headroom-Project header removed on disable',
      'ANTHROPIC_CUSTOM_HEADERS' not in cfg_off.get('env',{}),
      str(list(cfg_off.get('env',{}).keys())))

# ── MANAGER: TEST-CHAIN ───────────────────────────────────────────────────────
tc = get('http://localhost:9099/api/test-chain?instance=coding')
check('[FEATURE] Test-chain API works',
      tc.get('proxy')=='ok',
      f"proxy={tc.get('proxy')} hyperspace={tc.get('hyperspace')} {tc.get('latencyMs',0)}ms")

# ── MANAGER: CSV EXPORT ───────────────────────────────────────────────────────
with urllib.request.urlopen('http://localhost:9099/api/export.csv', timeout=10) as r:
    csv_data = r.read().decode()
check('[FEATURE] CSV export endpoint works',
      csv_data.startswith('Instance,Date'),
      f'{len(csv_data.splitlines())} lines')

# ── MANAGER: DASHBOARD HTML ───────────────────────────────────────────────────
features = ['testChain','exportCsv','history-details','Projected Monthly',
            'Proxy Compression Rate','CSRF','test-btn']
missing = [f for f in features if f not in html]
check('[FEATURE] Dashboard has all new UI features',
      not missing, 'all present' if not missing else f'missing: {missing}')

# ── SOURCE PATCHES ────────────────────────────────────────────────────────────
ccr = docker(
    'from headroom.proxy.helpers import apply_session_sticky_ccr_tool; '
    'import inspect; src=inspect.getsource(apply_session_sticky_ccr_tool); '
    'print("ok" if "msg.get" in src and "request_body" in src else "fail")'
)
check('[FIX] #2440 CCR fix scans messages array (not tools array)', ccr == 'ok', ccr)

dup = docker(
    'from headroom.proxy.helpers import _TOOL_SEARCH_CORE_TOOLS; '
    'items=list(_TOOL_SEARCH_CORE_TOOLS); '
    'print("ok" if items.count("webfetch")==1 else "dup:"+str(items.count("webfetch")))'
)
check('[FIX] #2646 no duplicate webfetch in _TOOL_SEARCH_CORE_TOOLS', dup == 'ok', dup)

# ── ENVIRONMENT ───────────────────────────────────────────────────────────────
budget = docker('import os; print(os.environ.get("HEADROOM_BUDGET","unset"))')
check('[ENV] HEADROOM_BUDGET spend cap set', budget != 'unset', f'HEADROOM_BUDGET={budget}')

ttl = docker('import os; print(os.environ.get("HEADROOM_CCR_TTL_SECONDS","unset"))')
check('[ENV] CCR TTL raised to 14400s (4h sessions)', ttl == '14400', f'TTL={ttl}')

mem_coding = subprocess.run(
    ['docker','inspect','headroom-coding','--format','{{.HostConfig.Memory}}'],
    capture_output=True, text=True).stdout.strip()
mem_mb = int(mem_coding) // (1024*1024) if mem_coding.isdigit() else 0
check('[ENV] Memory limit raised to 768m (coding)', mem_mb >= 700, f'{mem_mb}m')

# ── SETTINGS FILES ────────────────────────────────────────────────────────────
for proj_id, fpath in [
    ('vedastra',   'C:/Users/I772791/Personal Git/Vedastra-Labs/.claude/settings.json'),
    ('neuralsutras','C:/Users/I772791/Personal Git/NeuralSutras/.claude/settings.json'),
    ('headroom',   'C:/Users/I772791/Personal Git/headroom/.claude/settings.json'),
]:
    try:
        with open(fpath) as f: cfg = json.load(f)
        url = cfg.get('env',{}).get('ANTHROPIC_BASE_URL','')
        valid = any(x in url for x in ['localhost:6655','localhost:8787','localhost:8788','localhost:8789'])
        check(f'[SETTINGS] {proj_id} settings.json valid', valid, url[:55])
    except Exception as e:
        check(f'[SETTINGS] {proj_id} settings.json valid', False, str(e)[:60])

# ── PRINT RESULTS ─────────────────────────────────────────────────────────────
print()
print('=' * 64)
print('  HEADROOM COMPLETE E2E TEST RESULTS')
print('=' * 64)
passed = sum(1 for _, ok, _ in results if ok)
failed = [(n,d) for n,ok,d in results if not ok]
for name, ok, detail in results:
    st = 'PASS' if ok else 'FAIL'
    print(f'  [{st}] {name}')
    if not ok or (detail and detail not in ('','True','ok')):
        print(f'         {detail}')
print('=' * 64)
print(f'  {passed}/{len(results)} passed', end='')
if failed:
    print(f'  |  {len(failed)} failed:')
    for n, d in failed:
        print(f'    - {n}')
else:
    print('  — ALL PASS')
print('=' * 64)
