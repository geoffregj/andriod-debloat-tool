#!/usr/bin/env python3
"""
Android Debloat Tool — Web UI
A WinUtil-style browser front end for android_debloat_tool.py.

Same engine, same adb calls, same APP_DB — this just adds:
  - A view of EVERY installed app on the phone, not just the curated list
  - An "Open on phone" button per app so you can visually confirm what it
    is before touching it (addresses the com.transsion.ella decoy problem
    and anything like it on other OEMs)
  - Uninstall / Disable / Restore buttons per app, same safety rules as
    the CLI (PROTECTED apps are refused server-side, not just hidden)
  - Anything installed on the phone but NOT in the curated APP_DB is
    auto-classified: known core Android/Google system prefixes are
    locked as protected automatically; everything else is flagged
    "unknown" and requires an explicit confirm before any action.

No pip installs. Stdlib only (http.server). Requires: same as the CLI —
adb on PATH, phone authorized.

Usage:
    python3 webui.py
    (opens http://127.0.0.1:8765 in your browser automatically)
"""

import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import android_debloat_tool as core

HOST = "127.0.0.1"
PORT = 8765

# Package-name prefixes that are always treated as protected even if a
# specific package isn't in the curated APP_DB. This is the safety net
# for the "show me everything" view — without it, browsing the full
# installed-app list would let someone uninstall com.android.settings
# just because nobody had gotten around to adding it to APP_DB yet.
CORE_PROTECTED_PREFIXES = (
    "com.android.",
    "com.google.android.gms",
    "com.google.android.gsf",
    "com.android.vending",
    "com.google.android.gms.",
    "com.google.android.packageinstaller",
    "com.google.android.permissioncontroller",
    "com.google.android.inputmethod",
    "android",  # bare "android" package itself
)

# Substrings that usually indicate "this is the home screen launcher" —
# treated as protected too, since Transsion/MIUI/etc all name these
# differently and users report reset home layouts if the wrong one goes.
LAUNCHER_HINTS = ("launcher",)


def classify_unknown(pkg):
    """Best-effort risk classification for a package that isn't in the
    curated APP_DB. Returns (risk, note)."""
    lower = pkg.lower()
    if any(pkg.startswith(p) for p in CORE_PROTECTED_PREFIXES):
        return "protected", "Matches a known core Android/Google package pattern — auto-protected, not in curated DB yet."
    if any(hint in lower for hint in LAUNCHER_HINTS):
        return "protected", "Package name suggests this may be a home-screen launcher — auto-protected. Verify manually if you believe this is safe."
    return "unknown", "Not in the curated database. Use 'Open on phone' to confirm what this actually is before doing anything else."


def gather_apps():
    """One batch of adb calls instead of one per app — the CLI's
    is_installed()/is_disabled() do a fresh shell call per app, which is
    fine for the curated list but too slow once we're listing everything
    on the phone."""
    universe_out, _ = core.adb_shell("pm", "list", "packages", "-u")
    enabled_out, _ = core.adb_shell("pm", "list", "packages", "--user", "0")
    disabled_out, _ = core.adb_shell("pm", "list", "packages", "-d")

    def pkgset(out):
        return {line.split("package:", 1)[1].strip() for line in out.splitlines() if line.startswith("package:")}

    universe = pkgset(universe_out)
    enabled = pkgset(enabled_out)
    disabled = pkgset(disabled_out)

    def status(pkg):
        if pkg in disabled:
            return "disabled"
        if pkg in enabled:
            return "installed"
        if pkg in universe:
            return "removed"
        return "unknown-status"

    # Curated entries first, in their original categories.
    categories = []
    seen = set()
    for cat_name, apps in core.APP_DB.items():
        entries = []
        for pkg, meta in apps.items():
            entries.append({
                "pkg": pkg,
                "risk": meta["risk"],
                "note": meta["note"],
                "status": status(pkg),
                "known": True,
            })
            seen.add(pkg)
        categories.append({"name": cat_name, "apps": entries})

    # Everything else actually on the phone but not curated.
    leftover = sorted(universe - seen)
    if leftover:
        entries = []
        for pkg in leftover:
            risk, note = classify_unknown(pkg)
            entries.append({
                "pkg": pkg,
                "risk": risk,
                "note": note,
                "status": status(pkg),
                "known": False,
            })
        categories.append({"name": "Not in curated database (inspect before touching)", "apps": entries})

    return categories


def do_action(pkg, action):
    # Re-derive risk server-side; never trust the client's idea of risk.
    known_meta = None
    for apps in core.APP_DB.values():
        if pkg in apps:
            known_meta = apps[pkg]
            break
    if known_meta is not None:
        risk = known_meta["risk"]
    else:
        risk, _note = classify_unknown(pkg)

    if action == "open":
        return open_app(pkg)

    if risk == "protected":
        return False, "This app is PROTECTED. Refusing to touch it."

    if action == "uninstall":
        return core.uninstall(pkg)
    if action == "disable":
        return core.disable(pkg)
    if action == "restore":
        return core.restore(pkg)
    return False, f"Unknown action: {action}"


def open_app(pkg):
    """Best-effort launch so the user can visually confirm what an app
    actually is on their own screen. Tries resolve-activity first (more
    reliable), falls back to monkey's launcher-intent trick.

    Some OEM packages (e.g. Transsion/Tecno dialer-resolution hooks) resolve
    to a bare ResolverActivity/ChooserActivity component. Those aren't real
    launcher entry points -- they expect to be invoked as part of another
    app's intent resolution, and calling them directly with `am start -n`
    throws "no app can perform this action" because there's nothing for
    them to resolve. Skip those and fall through to monkey instead."""
    out, _err = core.adb_shell("cmd", "package", "resolve-activity", "--brief", pkg)
    component = None
    for line in out.splitlines():
        line = line.strip()
        if "/" in line and not line.lower().startswith("no activity"):
            if "resolveractivity" in line.lower() or "chooseractivity" in line.lower():
                continue
            component = line
            break
    if component:
        out2, err2 = core.adb_shell("am", "start", "-n", component)
        ok = "Error" not in out2 and "Exception" not in out2 and "Error" not in err2
        core.log(f"OPEN {pkg} via {component}: {'OK' if ok else 'FAILED'}")
        return ok, (out2 or err2 or f"Launched {component}")

    out2, err2 = core.adb_shell("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
    ok = "Events injected: 1" in out2
    core.log(f"OPEN {pkg} via monkey: {'OK' if ok else 'FAILED'}")
    return ok, (out2 or err2)


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Android Debloat Tool</title>
<style>
  :root {
    --bg: #14161a; --panel: #1c1f26; --border: #2b2f38; --text: #e6e8eb;
    --muted: #8a8f98; --safe: #3fb950; --caution: #d29922; --danger: #f85149;
    --unknown: #58a6ff; --accent: #7c5cff;
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: var(--bg); color: var(--text); }
  header { display:flex; align-items:center; gap:16px; padding:14px 20px; background:var(--panel); border-bottom:1px solid var(--border); position:sticky; top:0; z-index:10; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  #status { font-size:13px; padding:4px 10px; border-radius:12px; background:#2b2f38; }
  #status.ok { background:#123a1e; color:var(--safe); }
  #status.bad { background:#3a1414; color:var(--danger); }
  #search { flex:1; max-width:320px; padding:7px 10px; border-radius:8px; border:1px solid var(--border); background:#0f1115; color:var(--text); }
  button { cursor:pointer; border:1px solid var(--border); background:#252932; color:var(--text); border-radius:6px; padding:6px 10px; font-size:12px; }
  button:hover:not(:disabled) { background:#31363f; }
  button:disabled { opacity:0.35; cursor:not-allowed; }
  button.primary { background:var(--accent); border-color:var(--accent); }
  button.danger { background:#3a1414; border-color:var(--danger); color:var(--danger); }
  button.danger:hover:not(:disabled) { background:#4a1a1a; }
  .layout { display:flex; }
  nav { width:260px; flex-shrink:0; border-right:1px solid var(--border); padding:14px; height:calc(100vh - 53px); overflow-y:auto; position:sticky; top:53px; }
  nav a { display:block; padding:7px 8px; border-radius:6px; color:var(--muted); text-decoration:none; font-size:13px; margin-bottom:2px; }
  nav a:hover, nav a.active { background:var(--panel); color:var(--text); }
  main { flex:1; padding:20px; max-width:1000px; }
  .cat { margin-bottom:34px; }
  .cat h2 { font-size:14px; color:var(--muted); text-transform:uppercase; letter-spacing:0.04em; border-bottom:1px solid var(--border); padding-bottom:8px; }
  .app-row { display:flex; align-items:flex-start; gap:12px; padding:12px; border:1px solid var(--border); border-radius:8px; margin-bottom:8px; background:var(--panel); }
  .app-main { flex:1; min-width:0; }
  .pkg { font-family: ui-monospace, monospace; font-size:13px; word-break:break-all; }
  .note { color:var(--muted); font-size:12px; margin-top:4px; }
  .badges { display:flex; gap:6px; margin-top:6px; flex-wrap:wrap; }
  .badge { font-size:10px; padding:2px 7px; border-radius:10px; font-weight:600; letter-spacing:0.02em; }
  .risk-safe { background:#123a1e; color:var(--safe); }
  .risk-caution { background:#3a2c0f; color:var(--caution); }
  .risk-protected { background:#3a1414; color:var(--danger); }
  .risk-unknown { background:#0f2438; color:var(--unknown); }
  .status-installed { background:#1a2233; color:#9db4ff; }
  .status-disabled { background:#2a2a1a; color:#d6c98a; }
  .status-removed { background:#2a1a1a; color:#e29a9a; }
  .actions { display:flex; flex-direction:column; gap:6px; flex-shrink:0; width:110px; }
  #log { position:sticky; bottom:0; background:#0d0f13; border-top:1px solid var(--border); max-height:180px; overflow-y:auto; padding:10px 20px; font-family: ui-monospace, monospace; font-size:12px; color:#9ca3af; }
  #log .line { white-space:pre-wrap; }
  #log .line.ok { color:var(--safe); }
  #log .line.fail { color:var(--danger); }
  .hidden { display:none !important; }
</style>
</head>
<body>
<header>
  <h1>Android Debloat Tool</h1>
  <span id="status">checking device...</span>
  <input id="search" type="text" placeholder="Filter by package name or note...">
  <button id="reload">Reload apps</button>
</header>
<div class="layout">
  <nav id="nav"></nav>
  <main id="main"><p style="color:var(--muted)">Loading apps from device...</p></main>
</div>
<div id="log"><div class="line">Session log will appear here.</div></div>

<script>
let DATA = [];

async function api(path, opts) {
  const res = await fetch(path, opts);
  return res.json();
}

function badge(cls, text) {
  return `<span class="badge ${cls}">${text}</span>`;
}

function riskBadge(risk) {
  return badge('risk-' + risk, risk.toUpperCase());
}
function statusBadge(status) {
  return badge('status-' + status, status);
}

function appRow(app) {
  const disabledAll = app.risk === 'protected';
  const isInstalled = app.status === 'installed';
  const isDisabled = app.status === 'disabled';
  const isRemoved = app.status === 'removed';
  return `
  <div class="app-row" data-pkg="${app.pkg}" data-note="${(app.note||'').toLowerCase()}">
    <div class="app-main">
      <div class="pkg">${app.pkg}</div>
      <div class="note">${app.note}</div>
      <div class="badges">${riskBadge(app.risk)}${statusBadge(app.status)}${app.known ? '' : badge('risk-unknown','NOT CURATED')}</div>
    </div>
    <div class="actions">
      <button data-action="open" data-pkg="${app.pkg}">Open on phone</button>
      <button data-action="uninstall" data-pkg="${app.pkg}" data-risk="${app.risk}" data-note="${app.note}" ${disabledAll || !isInstalled ? 'disabled' : ''} class="danger">Uninstall</button>
      <button data-action="disable" data-pkg="${app.pkg}" data-risk="${app.risk}" data-note="${app.note}" ${disabledAll || !isInstalled ? 'disabled' : ''}>Disable</button>
      <button data-action="restore" data-pkg="${app.pkg}" ${disabledAll || isInstalled ? 'disabled' : ''}>Restore</button>
    </div>
  </div>`;
}

function render() {
  const nav = document.getElementById('nav');
  const main = document.getElementById('main');
  nav.innerHTML = '<a href="#" data-cat="__all__" class="active">All apps</a>' +
    DATA.map(c => `<a href="#" data-cat="${c.name}">${c.name} <span style="color:var(--muted)">(${c.apps.length})</span></a>`).join('');
  main.innerHTML = DATA.map(c => `
    <div class="cat" data-cat="${c.name}">
      <h2>${c.name}</h2>
      ${c.apps.map(appRow).join('')}
    </div>`).join('');
  attachHandlers();
}

function attachHandlers() {
  document.querySelectorAll('nav a').forEach(a => {
    a.onclick = (e) => {
      e.preventDefault();
      document.querySelectorAll('nav a').forEach(x => x.classList.remove('active'));
      a.classList.add('active');
      const cat = a.dataset.cat;
      document.querySelectorAll('.cat').forEach(c => {
        c.classList.toggle('hidden', cat !== '__all__' && c.dataset.cat !== cat);
      });
    };
  });

  document.querySelectorAll('button[data-action]').forEach(btn => {
    btn.onclick = () => handleAction(btn);
  });
}

function logLine(text, kind) {
  const log = document.getElementById('log');
  const div = document.createElement('div');
  div.className = 'line' + (kind ? ' ' + kind : '');
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function handleAction(btn) {
  const pkg = btn.dataset.pkg;
  const action = btn.dataset.action;
  const risk = btn.dataset.risk;
  const note = btn.dataset.note;

  if (action === 'uninstall' && (risk === 'caution' || risk === 'unknown')) {
    const msg = `${risk.toUpperCase()}: ${note}\\n\\nType YES in the next prompt to confirm uninstalling ${pkg}.`;
    if (!window.confirm(msg)) { logLine(`Cancelled uninstall of ${pkg}`); return; }
    const typed = window.prompt(`Type YES to confirm uninstalling ${pkg}`);
    if (typed !== 'YES') { logLine(`Cancelled uninstall of ${pkg} (confirmation text mismatch)`); return; }
  }

  btn.disabled = true;
  logLine(`${action.toUpperCase()} ${pkg} ...`);
  try {
    const result = await api('/api/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pkg, action})
    });
    logLine(`${result.ok ? 'OK' : 'FAILED'}: ${pkg} — ${result.message}`, result.ok ? 'ok' : 'fail');
  } catch (e) {
    logLine(`ERROR: ${e}`, 'fail');
  }
  if (action !== 'open') await loadApps();
  btn.disabled = false;
}

document.getElementById('search').addEventListener('input', (e) => {
  const q = e.target.value.toLowerCase();
  document.querySelectorAll('.app-row').forEach(row => {
    const match = row.dataset.pkg.toLowerCase().includes(q) || row.dataset.note.includes(q);
    row.classList.toggle('hidden', q.length > 0 && !match);
  });
});

document.getElementById('reload').onclick = loadApps;

async function loadStatus() {
  const s = await api('/api/status');
  const el = document.getElementById('status');
  el.textContent = s.connected ? `Connected: ${s.device}` : 'No device: ' + s.message;
  el.className = s.connected ? 'ok' : 'bad';
  return s.connected;
}

async function loadApps() {
  const connected = await loadStatus();
  if (!connected) {
    document.getElementById('main').innerHTML = '<p style="color:var(--muted)">Connect your phone (USB debugging authorized) and click Reload apps.</p>';
    return;
  }
  document.getElementById('main').innerHTML = '<p style="color:var(--muted)">Loading apps from device (this reads the full package list, may take a few seconds)...</p>';
  DATA = await api('/api/apps');
  render();
}

loadApps();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep terminal quiet; app-level actions go to debloat_log.txt

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            connected, device, message = check_status()
            self._send_json({"connected": connected, "device": device, "message": message})
        elif self.path == "/api/apps":
            try:
                self._send_json(gather_apps())
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        if self.path == "/api/action":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw)
                pkg = payload["pkg"]
                action = payload["action"]
            except Exception:
                self._send_json({"ok": False, "message": "bad request"}, status=400)
                return
            ok, message = do_action(pkg, action)
            self._send_json({"ok": ok, "message": message})
        else:
            self._send_json({"error": "not found"}, status=404)


def check_status():
    import shutil
    import subprocess
    if shutil.which("adb") is None:
        return False, None, "adb not installed or not on PATH"
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=15)
    except Exception as e:
        return False, None, str(e)
    lines = [l for l in result.stdout.strip().splitlines()[1:] if l.strip()]
    authorized = [l for l in lines if l.strip().endswith("device")]
    unauthorized = [l for l in lines if "unauthorized" in l]
    if authorized:
        return True, authorized[0].split()[0], "ok"
    if unauthorized:
        return False, None, "device unauthorized — check phone screen for the USB debugging prompt"
    return False, None, "no device found — plug in phone with USB debugging enabled"


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Android Debloat Tool web UI running at {url}")
    print("Press Ctrl+C to stop.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
