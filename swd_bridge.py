#!/usr/bin/env python3
"""swd-bridge: el puente entre esta laptop y smart-web-dev.com para mirar y manejar tmux desde el
teléfono o el Kindle. Plan y modelo de amenaza: SWDprod `docs-ia/plan-control-remoto.md`; servidor: app `remote`.

    teléfono/Kindle ──► VPS /remote/ (2FA) ◄── esta laptop pregunta cada 2–10 s («¿hay algo para mí?»)

La laptop NUNCA escucha en la red: solo abre conexiones hacia la VPS. Ejecuta únicamente un vocabulario CERRADO
de verbos, validado acá otra vez aunque la VPS ya lo validó (si la VPS cayera en malas manos, no puede pedir más
que esto).

    Lectura:
    capture <pane> [lines]      pantalla de un panel
    server-log <window> [lines] el panel del servidor de un proyecto
    git-status <window>         status + últimos commits del repo de esa ventana
    git-diff <window> [mode]    stat | full | staged (sin rutas ni opciones libres)
    Escritura (F3; la VPS exige 2FA reciente, y acá se rechaza una escritura que llegue con más de 45 s):
    send <pane> <text>          pega el texto en un panel de AGENTE (claude/codex, comprobado en vivo) + Enter
    key <pane> <key>            una tecla de una lista fija a un panel de agente
    server-restart <window>     Ctrl-C + el comando de servidor del proyecto, leído de ESTE projects.json
    start <project> <profile>   abre el proyecto con el launcher, agente SIN saltarse permisos (--remote)
    No hay verbo que ejecute un texto como comando: la shell libre está apagada.

Uso:
    python3 swd_bridge.py                  # en primer plano (Ctrl+C sale)
    python3 swd_bridge.py --background     # segundo plano (log: ~/.config/swd-bridge/bridge.log)
    python3 swd_bridge.py --status | --stop
    python3 swd_bridge.py --snapshot       # lo que vería el teléfono (local, sin servidor)
    python3 swd_bridge.py --exec capture pane=%3   # ejecuta un verbo local, sin servidor (pruebas)

Configuración: ~/.config/swd-bridge/bridge.env (chmod 600) con REMOTE_URL y REMOTE_TOKEN.
Apagado de emergencia SIN tocar el servidor: `touch ~/.config/swd-bridge/DISABLED` (deja de preguntar).
Auditoría local, que la VPS no puede borrar: ~/.config/swd-bridge/audit.jsonl.
Solo stdlib.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

VERSION = '2'
BRIDGE_DIR = Path(os.environ.get('SWD_BRIDGE_DIR') or Path.home() / '.config' / 'swd-bridge')
ENV_FILE = BRIDGE_DIR / 'bridge.env'
STATE_DIR = BRIDGE_DIR / 'state'          # lo escriben los hooks de Claude/Codex (swd_bridge_hook.py)
DISABLED = BRIDGE_DIR / 'DISABLED'
AUDIT = BRIDGE_DIR / 'audit.jsonl'
PID_FILE = BRIDGE_DIR / 'bridge.pid'
LOG_FILE = BRIDGE_DIR / 'bridge.log'
API = '/api/remote/agent/'
ERROR_BACKOFF = 10.0
DISABLED_BACKOFF = 30.0
FULL_SNAPSHOT_EVERY = 60.0      # s: aunque nada cambie, se manda la foto entera de vez en cuando
MAX_OUTPUT = 200 * 1024
AGENT_COMMANDS = {'claude', 'codex'}
SHELLS = {'zsh', '-zsh', 'bash', '-bash', 'sh', 'fish', 'login'}
WRITE_MAX_AGE = 45.0            # s: una escritura que llega más tarde (laptop dormida, red) no se ejecuta
HERE = Path(__file__).resolve().parent
LAUNCHER = Path(os.environ.get('SWD_BRIDGE_LAUNCHER') or HERE / 'nexus-launcher.py')   # override solo para pruebas
PROJECTS_FILE = Path(os.environ.get('SWD_BRIDGE_PROJECTS') or HERE / 'nexus-private' / 'projects.json')
# Para las pruebas: otro servidor de tmux (`tmux -L <nombre>`), así nunca se toca la sesión real.
TMUX = ['tmux'] + (['-L', os.environ['SWD_BRIDGE_TMUX_SOCKET']] if os.environ.get('SWD_BRIDGE_TMUX_SOCKET') else [])

# ---- Vocabulario (MISMO contrato que SWDprod remote/verbs.py) --------------------------------------------
PANE_RE = re.compile(r'^%\d{1,6}$')
WINDOW_RE = re.compile(r'^@\d{1,6}$')
MAX_LINES, DEFAULT_LINES = 400, 120
DIFF_MODES = ('stat', 'full', 'staged')
VERBS = {
    'capture': ('pane', 'lines'),
    'server-log': ('window', 'lines'),
    'git-status': ('window',),
    'git-diff': ('window', 'mode'),
    'send': ('pane', 'text'),
    'key': ('pane', 'key'),
    'server-restart': ('window',),
    'start': ('project', 'profile'),
}
WRITE_VERBS = frozenset({'send', 'key', 'server-restart', 'start'})
MAX_TEXT = 4000
KEYS = ('enter', 'esc', 'ctrl-c', 'y', 'n', '1', '2', '3', '4', 'up', 'down', 'tab', 'shift-tab')
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$')
TMUX_KEYS = {'enter': 'Enter', 'esc': 'Escape', 'ctrl-c': 'C-c', 'y': 'y', 'n': 'n', '1': '1', '2': '2', '3': '3',
             '4': '4', 'up': 'Up', 'down': 'Down', 'tab': 'Tab', 'shift-tab': 'BTab'}

_stop = False


class VerbError(ValueError):
    pass


def clean(verb: str, args: dict | None) -> dict:
    if verb not in VERBS:
        raise VerbError(f'verbo desconocido: {verb!r}')
    args = dict(args or {})
    unknown = set(args) - set(VERBS[verb])
    if unknown:
        raise VerbError(f'argumentos no permitidos: {", ".join(sorted(unknown))}')
    out = {}
    for name in VERBS[verb]:
        value = args.get(name)
        if name == 'pane':
            if not isinstance(value, str) or not PANE_RE.match(value):
                raise VerbError('pane inválido')
            out[name] = value
        elif name == 'window':
            if not isinstance(value, str) or not WINDOW_RE.match(value):
                raise VerbError('window inválido')
            out[name] = value
        elif name == 'lines':
            try:
                n = DEFAULT_LINES if value in (None, '') else int(value)
            except (TypeError, ValueError):
                raise VerbError('lines inválido') from None
            out[name] = max(1, min(n, MAX_LINES))
        elif name == 'mode':
            value = value or 'stat'
            if value not in DIFF_MODES:
                raise VerbError('mode inválido')
            out[name] = value
        elif name == 'text':
            out[name] = clean_text(value)
        elif name == 'key':
            if value not in KEYS:
                raise VerbError('key inválida')
            out[name] = value
        elif name in ('project', 'profile'):
            if not isinstance(value, str) or not NAME_RE.match(value):
                raise VerbError(f'{name} inválido')
            out[name] = value
    return out


def clean_text(value) -> str:
    """Igual que en la VPS: sin caracteres de control salvo salto de línea y tab (ninguna secuencia de escape llega a
    la terminal), no vacío y con tope."""
    if not isinstance(value, str):
        raise VerbError('text inválido')
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    value = ''.join(ch for ch in value if ch in '\n\t' or (ord(ch) >= 32 and ord(ch) != 127)).strip()
    if not value:
        raise VerbError('el texto está vacío')
    if len(value) > MAX_TEXT:
        raise VerbError('texto demasiado largo')
    return value


# ---- Secretos: se tachan antes de que nada salga de la laptop -----------------------------------------------
REDACT = [
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', re.S), '‹clave privada›'),
    (re.compile(r'\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}'), '‹api-key›'),
    (re.compile(r'\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}'), '‹github-token›'),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), '‹aws-key›'),
    (re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'), '‹google-key›'),
    (re.compile(r'\bxox[abprs]-[A-Za-z0-9-]{10,}'), '‹slack-token›'),
    (re.compile(r'(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{16,}'), r'\1 ‹token›'),
    (re.compile(r'(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY)[A-Z0-9_]*)'
                r'(\s*[:=]\s*)(["\']?)[^\s"\']{6,}\3'), r'\1\2‹redactado›'),
    (re.compile(r'(?i)(://[^/\s:@]+:)[^@\s/]{4,}(@)'), r'\1‹redactado›\2'),      # usuario:clave@host en URLs
]


def redact(text: str) -> str:
    for rx, repl in REDACT:
        text = rx.sub(repl, text)
    return text


# ---- tmux ---------------------------------------------------------------------------------------------------
FIELDS = ('session', 'window_id', 'window_index', 'window_name', 'window_active', 'pane_id', 'pane_index',
          'command', 'cwd', 'pane_active', 'window_panes')
FORMAT = '\t'.join('#{%s}' % f for f in (
    'session_name', 'window_id', 'window_index', 'window_name', 'window_active', 'pane_id', 'pane_index',
    'pane_current_command', 'pane_current_path', 'pane_active', 'window_panes'))


def _run(cmd: list[str], cwd: str | None = None, timeout: float = 15, env: dict | None = None) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return res.returncode, (res.stdout if res.returncode == 0 else (res.stderr or res.stdout))


def _agent_state(pane_id: str) -> dict | None:
    try:
        data = json.loads((STATE_DIR / f'{pane_id.lstrip("%")}.json').read_text())
        return {'state': data.get('state'), 'agent': data.get('agent'), 'since': data.get('ts')}
    except (OSError, ValueError):
        return None


def _ports() -> dict:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import swd_registry
        return {**swd_registry.PINNED_PORTS, **swd_registry.load().get('ports', {})}
    except Exception:  # noqa: BLE001 — sin registro, la foto sale igual (sin puertos)
        return {}


def snapshot() -> dict:
    code, out = _run([*TMUX, 'list-panes', '-a', '-F', FORMAT], timeout=5)
    if code != 0:
        return {'tmux': False, 'windows': [], 'projects': sorted(load_projects()), 'profiles': _profiles(),
                'ts': time.time()}
    ports = _ports()
    windows: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) != len(FIELDS):
            continue
        row = dict(zip(FIELDS, parts))
        win = windows.setdefault(row['window_id'], {
            'id': row['window_id'], 'session': row['session'], 'index': int(row['window_index']),
            'name': row['window_name'], 'active': row['window_active'] == '1',
            'port': ports.get(row['window_name']), 'panes': []})
        idx, n = int(row['pane_index']), int(row['window_panes'])
        cmd = row['command']
        if cmd in AGENT_COMMANDS:
            role = 'agent'
        elif n == 3 and idx == 2:
            role = 'server'          # layout del launcher: columna derecha = servidor
        elif idx == 0:
            role = 'main'
        else:
            role = 'shell'
        pane = {'id': row['pane_id'], 'index': idx, 'role': role, 'command': cmd, 'cwd': row['cwd'],
                'active': row['pane_active'] == '1'}
        if role == 'agent':
            st = _agent_state(row['pane_id'])
            if st:
                pane['agent_state'], pane['agent_since'] = st['state'], st['since']
        win['panes'].append(pane)
    return {'tmux': True, 'windows': sorted(windows.values(), key=lambda w: (w['session'], w['index'])),
            'projects': sorted(load_projects()), 'profiles': _profiles(), 'ts': time.time()}


def load_projects() -> dict:
    """alias → proyecto del launcher (projects.json de ESTA laptop: lo único que `start` y `server-restart` aceptan)."""
    try:
        data = json.loads(PROJECTS_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return {p['alias']: p for p in data if isinstance(p, dict) and NAME_RE.match(str(p.get('alias', '')))}


def _registry():
    sys.path.insert(0, str(HERE))
    import swd_registry
    return swd_registry


def _profiles() -> dict:
    try:
        reg = _registry()
        return {name: reg.describe(spec) for name, spec in reg.profiles().items()}
    except Exception:  # noqa: BLE001
        return {}


def _find(snap: dict, *, window: str | None = None, pane: str | None = None):
    for w in snap.get('windows', []):
        if window and w['id'] == window:
            return w
        for p in w['panes']:
            if pane and p['id'] == pane:
                return p
    raise VerbError(f'no existe {window or pane} (¿se cerró?)')


def _capture(pane_id: str, lines: int) -> str:
    code, out = _run([*TMUX, 'capture-pane', '-p', '-J', '-t', pane_id, '-S', f'-{lines}'], timeout=5)
    if code != 0:
        raise VerbError(f'no pude leer el panel: {out.strip()[:200]}')
    return redact(out.rstrip('\n') + '\n')


def _git(cwd: str, *args: str) -> str:
    # Opciones que un repo podría usar para ejecutar código al leerlo (fsmonitor, hooks, diff externo, textconv,
    # pager) quedan apagadas por línea de comandos: el comando es fijo, el repo no decide nada.
    cmd = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-c', 'core.pager=cat',
           '--no-pager', '-C', cwd, *args]
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_PAGER': 'cat', 'PAGER': 'cat'}
    code, out = _run(cmd, timeout=20, env=env)
    if code != 0:
        raise VerbError(f'git falló: {out.strip()[:300]}')
    return out


def _repo_dir(window: dict) -> str:
    panes = sorted(window['panes'], key=lambda p: p['index'])
    return panes[0]['cwd'] if panes else ''


def _pane_command(pane_id: str) -> str:
    code, out = _run([*TMUX, 'display-message', '-p', '-t', pane_id, '#{pane_current_command}'], timeout=5)
    return out.strip() if code == 0 else ''


def _tmux_ok(*args: str, stdin: str | None = None) -> None:
    try:
        res = subprocess.run([*TMUX, *args], input=stdin, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerbError(f'tmux falló: {exc}') from None
    if res.returncode != 0:
        raise VerbError(f'tmux falló: {(res.stderr or res.stdout).strip()[:200]}')


def _require_agent(pane_id: str, snap: dict) -> None:
    _find(snap, pane=pane_id)
    cmd = _pane_command(pane_id)                 # EN VIVO, no la foto: el agente pudo haber salido hace un segundo
    if cmd not in AGENT_COMMANDS:
        raise VerbError(f'ese panel ya no tiene un agente (corre «{cmd or "?"}»): no se escribe en una terminal')


def _send(pane_id: str, text: str, snap: dict) -> str:
    _require_agent(pane_id, snap)
    # Pegado con «bracketed paste» (-p): el agente recibe el texto como UN pegado, con sus saltos de línea, y recién
    # el Enter de después lo manda. Un buffer con nombre propio que se borra al pegar (-d).
    _tmux_ok('load-buffer', '-b', 'swd-bridge', '-', stdin=text)
    _tmux_ok('paste-buffer', '-p', '-d', '-b', 'swd-bridge', '-t', pane_id)
    time.sleep(0.3)
    _tmux_ok('send-keys', '-t', pane_id, 'Enter')
    return f'enviado ({len(text)} caracteres)\n'


def _key(pane_id: str, key: str, snap: dict) -> str:
    _require_agent(pane_id, snap)
    _tmux_ok('send-keys', '-t', pane_id, TMUX_KEYS[key])
    return f'tecla {key} enviada\n'


def _wait_for_shell(pane_id: str, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _pane_command(pane_id) in SHELLS:
            return True
        time.sleep(0.3)
    return _pane_command(pane_id) in SHELLS


def _server_restart(window: dict) -> str:
    server = next((p for p in window['panes'] if p['role'] == 'server'), None)
    if not server:
        raise VerbError('esa ventana no tiene panel de servidor')
    project = load_projects().get(window['name'])
    panes = (project or {}).get('panes_config') or []
    cmd = (panes[1].get('custom_cmd') if len(panes) >= 2 else '') or ''
    if not cmd:
        raise VerbError(f'«{window["name"]}» no tiene comando de servidor en projects.json')
    pane = server['id']
    if _pane_command(pane) not in SHELLS:
        _tmux_ok('send-keys', '-t', pane, 'C-c')
        if not _wait_for_shell(pane, 8):
            _tmux_ok('send-keys', '-t', pane, 'C-c')
            if not _wait_for_shell(pane, 4):
                raise VerbError('el servidor no se detuvo con Ctrl-C; revísalo en la laptop')
    port = None
    try:
        reg = _registry()
        port = {**reg.PINNED_PORTS, **reg.load().get('ports', {})}.get(window['name'])
    except Exception:  # noqa: BLE001
        pass
    if '$PORT' in cmd and port:
        _tmux_ok('send-keys', '-t', pane, '-l', f'export PORT={int(port)}')
        _tmux_ok('send-keys', '-t', pane, 'Enter')
    _tmux_ok('send-keys', '-t', pane, '-l', cmd)        # el comando de projects.json, nunca uno que venga de la VPS
    _tmux_ok('send-keys', '-t', pane, 'Enter')
    time.sleep(2.5)
    return f'reiniciado: {cmd}\n\n' + _capture(pane, 15)


def _start(project: str, profile: str, snap: dict) -> str:
    if project not in load_projects():
        raise VerbError(f'«{project}» no está en projects.json')
    if profile not in _registry().profiles():
        raise VerbError(f'el perfil «{profile}» no existe')
    already = next((w for w in snap.get('windows', []) if w['name'] == project), None)
    if already:
        raise VerbError(f'«{project}» ya está abierto (ventana {already["id"]})')
    code, out = _run([*TMUX, 'list-sessions', '-F', '#{session_last_attached}\t#{session_name}'], timeout=5)
    sessions = sorted((line.split('\t') for line in out.splitlines() if '\t' in line), reverse=True) if code == 0 else []
    if sessions:
        session = sessions[0][1]
    else:
        session = 'remote'
        _tmux_ok('new-session', '-d', '-s', session, '-c', str(Path.home()))
    launch = (f'python3 {shlex.quote(str(LAUNCHER))} {shlex.quote(project)} --profile {shlex.quote(profile)} '
              f'--remote; exec zsh')
    try:
        res = subprocess.run([*TMUX, 'new-window', '-d', '-P', '-F', '#{window_id}', '-t', f'{session}:', '-n',
                              project, '-c', str(HERE), launch], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerbError(f'tmux falló: {exc}') from None
    if res.returncode != 0:
        raise VerbError(f'tmux falló: {res.stderr.strip()[:200]}')
    return f'abierto «{project}» con {profile} en la ventana {res.stdout.strip()} (sesión {session})\n'


def execute(verb: str, args: dict, snap: dict | None = None) -> str:
    args = clean(verb, args)
    snap = snap or snapshot()
    if verb == 'send':
        return _send(args['pane'], args['text'], snap)
    if verb == 'key':
        return _key(args['pane'], args['key'], snap)
    if verb == 'start':
        return _start(args['project'], args['profile'], snap)
    if verb == 'capture':
        _find(snap, pane=args['pane'])
        return _capture(args['pane'], args['lines'])
    window = _find(snap, window=args['window'])
    if verb == 'server-restart':
        return _server_restart(window)
    if verb == 'server-log':
        server = next((p for p in window['panes'] if p['role'] == 'server'), None)
        if not server:
            raise VerbError('esa ventana no tiene panel de servidor')
        return _capture(server['id'], args['lines'])
    cwd = _repo_dir(window)
    if not cwd or not Path(cwd).is_dir():
        raise VerbError('no encuentro la carpeta del proyecto')
    if verb == 'git-status':
        out = _git(cwd, 'status', '--short', '--branch') + '\n' + _git(cwd, 'log', '--oneline', '--decorate', '-8')
    else:
        base = ['diff', '--no-ext-diff', '--no-textconv', '--no-color']
        if args['mode'] == 'staged':
            base.append('--cached')
        out = _git(cwd, *base, '--stat') if args['mode'] == 'stat' else _git(cwd, *base)
        out = out or '(sin cambios)\n'
    out = redact(out)
    if len(out.encode()) > MAX_OUTPUT:
        out = out.encode()[:MAX_OUTPUT].decode(errors='ignore') + '\n… (recortado en la laptop)\n'
    return out


# ---- Servidor -----------------------------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f'{datetime.now():%Y-%m-%d %H:%M:%S} {msg}', flush=True)


def _sleep(seconds: float) -> None:
    end = time.monotonic() + seconds
    while not _stop and time.monotonic() < end:
        time.sleep(min(0.2, end - time.monotonic()))


def load_env() -> tuple[str, str]:
    if not ENV_FILE.exists():
        sys.exit(f'Falta {ENV_FILE} con REMOTE_URL y REMOTE_TOKEN (ver docs-ia/apps/remote.md).')
    if ENV_FILE.stat().st_mode & 0o077:
        sys.exit(f'{ENV_FILE} lo pueden leer otros usuarios: chmod 600 {ENV_FILE}')
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip().removeprefix('export ').strip()] = v.strip().strip('"').strip("'")
    url, token = env.get('REMOTE_URL', ''), env.get('REMOTE_TOKEN', '')
    if not url.startswith(('https://', 'http://localhost', 'http://127.0.0.1')) or len(token) < 32:
        sys.exit('REMOTE_URL debe ser https (o localhost para pruebas) y REMOTE_TOKEN de 32+ caracteres.')
    return url.rstrip('/'), token


def call(url: str, token: str, path: str, payload: dict, timeout: float = 20) -> tuple[int, dict]:
    req = urllib.request.Request(f'{url}{API}{path}', data=json.dumps(payload).encode(), method='POST',
                                 headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — URL propia
            return resp.status, json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b'{}')
        except ValueError:
            return e.code, {}


def audit(entry: dict) -> None:
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUDIT, 'a') as fh:
        fh.write(json.dumps({'ts': datetime.now().isoformat(timespec='seconds'), **entry}, ensure_ascii=False) + '\n')


def handle(url: str, token: str, job: dict, snap: dict) -> None:
    verb, args = job.get('verb', ''), job.get('args') or {}
    try:
        if verb in WRITE_VERBS and float(job.get('age') or 0) > WRITE_MAX_AGE:
            raise VerbError(f'llegó tarde ({job.get("age")} s): no se ejecuta una escritura vieja')
        out, ok, err = execute(verb, args, snap), True, ''
    except VerbError as exc:
        out, ok, err = '', False, str(exc)
    except Exception as exc:  # noqa: BLE001 — un fallo raro no tumba el puente
        out, ok, err = '', False, f'{type(exc).__name__}: {exc}'
    audit({'id': job.get('id'), 'verb': verb, 'args': args, 'ok': ok, 'bytes': len(out), 'error': err})
    log(f'#{job.get("id")} {verb} {args} → {"ok" if ok else "falló: " + err} ({len(out)} B)')
    try:
        call(url, token, f'result/{job["id"]}/', {'ok': ok, 'result': out, 'error': err})
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log(f'  no pude devolver el resultado ({exc}); el servidor lo dará por fallido')


def serve(once: bool = False) -> None:
    url, token = load_env()
    host = socket.gethostname().split('.')[0]
    log(f'swd-bridge v{VERSION} → {url} . Apagado de emergencia: touch {DISABLED}')
    last_hash, last_full, warned = '', 0.0, ''
    while not _stop:
        if DISABLED.exists():
            if warned != 'disabled':
                log(f'Apagado por {DISABLED}: no pregunto al servidor hasta que lo borres.')
                warned = 'disabled'
            _sleep(5)
            continue
        snap = snapshot()
        digest = hashlib.sha256(json.dumps({**snap, 'ts': 0}, sort_keys=True).encode()).hexdigest()
        payload = {'version': VERSION, 'host': host}
        send_snap = digest != last_hash or time.monotonic() - last_full > FULL_SNAPSHOT_EVERY
        if send_snap:
            payload['snapshot'] = snap
        try:
            status, body = call(url, token, 'claim/', payload)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if warned != 'net':
                log(f'sin conexión con el servidor ({exc}); reintento cada {ERROR_BACKOFF:.0f} s')
                warned = 'net'
            _sleep(ERROR_BACKOFF)
            continue
        if status != 200:
            if warned != f'http{status}':
                hint = ' (REMOTE_BRIDGE_ENABLED apagado o token sin configurar en el servidor)' if status == 503 else ''
                log(f'el servidor respondió {status}: {body.get("error")}{hint}')
                warned = f'http{status}'
            _sleep(DISABLED_BACKOFF if status in (401, 503) else ERROR_BACKOFF)
            continue
        if warned:
            log('conectado.')
            warned = ''
        if send_snap:
            last_hash, last_full = digest, time.monotonic()
        for job in body.get('commands', []):
            handle(url, token, job, snap)
        if once:
            return
        _sleep(max(1.0, min(float(body.get('poll_after') or 5), 30.0)))


# ---- Instancia única, segundo plano, estado ----------------------------------------------------------------

def _running_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _lock():
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    handle_ = open(BRIDGE_DIR / 'bridge.lock', 'w')
    try:
        fcntl.flock(handle_, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    PID_FILE.write_text(str(os.getpid()))
    return handle_


def main() -> None:
    p = argparse.ArgumentParser(description='Puente laptop ↔ smart-web-dev.com.')
    p.add_argument('--background', action='store_true')
    p.add_argument('--status', action='store_true')
    p.add_argument('--stop', action='store_true')
    p.add_argument('--once', action='store_true', help='Una sola pregunta al servidor y salir (pruebas).')
    p.add_argument('--snapshot', action='store_true', help='Imprimir la foto local y salir.')
    p.add_argument('--exec', nargs='+', metavar=('VERBO', 'CLAVE=VALOR'), help='Ejecutar un verbo local y salir.')
    args = p.parse_args()

    if args.snapshot:
        return print(json.dumps(snapshot(), ensure_ascii=False, indent=1))
    if args.exec:
        verb, kv = args.exec[0], dict(x.split('=', 1) for x in args.exec[1:] if '=' in x)
        try:
            return print(execute(verb, kv), end='')
        except VerbError as exc:
            sys.exit(f'error: {exc}')
    if args.status:
        pid = _running_pid()
        print(f'Proceso: {"corriendo (pid %s)" % pid if pid else "detenido"} · log: {LOG_FILE}'
              + (f' · APAGADO por {DISABLED}' if DISABLED.exists() else ''))
        return
    if args.stop:
        pid = _running_pid()
        if pid:
            os.kill(pid, signal.SIGTERM)      # por PID guardado, nunca por nombre
            print(f'Detenido (pid {pid}).')
        else:
            print('No estaba corriendo.')
        return
    if args.background:
        if _running_pid():
            return print(f'Ya está corriendo (pid {_running_pid()}). Log: {LOG_FILE}')
        load_env()                            # falla acá, a la vista, y no en el log
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a') as out:
            subprocess.Popen([sys.executable, os.path.abspath(__file__)], stdout=out, stderr=out,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        return print(f'swd-bridge en segundo plano. Log: {LOG_FILE}')

    lock = _lock()
    if lock is None:
        sys.exit(f'Ya hay otro swd-bridge corriendo (pid {_running_pid()}).')

    def _bye(*_):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGHUP, _bye)
    try:
        serve(once=args.once)
    except KeyboardInterrupt:
        pass
    finally:
        PID_FILE.unlink(missing_ok=True)
        log('Detenido.')


if __name__ == '__main__':
    main()
