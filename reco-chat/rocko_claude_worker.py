#!/usr/bin/env python3
"""Rocko con Claude: mientras corre, el bot de Telegram de reco contesta con la suscripción de Claude
de esta máquina (cuenta personal, `~/.claude-personal`) en lugar de con Gemini.

Cómo funciona (el detalle del lado del servidor está en SWDprod `reco/rocko_queue.py`):

  Telegram → webhook (VPS) → cola de Rocko ──► este proceso pregunta cada 2 s si hay algo
                                                (POST /api/reco/rocko/claim/, es también el latido)
                                             ◄── el mensaje + las instrucciones de Rocko
           claude -p (Sonnet, esfuerzo bajo, tools de reco por MCP)
                                             ──► respuesta (…/done/) o fallo (…/fail/)

Si esta máquina está apagada, dormida o sin el proyecto reco abierto, el servidor deja de verla
en ~15 s y contesta Gemini (flash → flash-lite), reintentando hasta que alguno pueda.

El mismo proceso atiende también a **EasyDoc** (SWDprod `api_apps/easydoc/claude_queue.py`): cuando un usuario
con la clave `use_claude_sonnet` (o un superuser) genera un documento, el servidor lo deja en una cola que
esta máquina consulta junto a la de Rocko (`/api/v1/easydoc/claude/laptop/claim/`). Se ejecuta con el modelo y
el esfuerzo que el servidor pide (Sonnet / alto), sin tools, y el texto vuelve por trozos para que el
navegador lo muestre mientras se escribe. Si esta máquina no está, EasyDoc usa Gemini. Cada trabajo corre en
su propio hilo, así Rocko y EasyDoc no se bloquean entre sí y el latido no se corta.

Uso:
  python3 rocko_claude_worker.py               # en primer plano (Ctrl+C para salir)
  python3 rocko_claude_worker.py --background  # en segundo plano, atado al panel de tmux actual
  python3 rocko_claude_worker.py --status      # ¿está corriendo? ¿qué ve el servidor?
  python3 rocko_claude_worker.py --stop
  python3 rocko_claude_worker.py --model opus --effort medium   # otro modelo para Rocko (default: sonnet / low)
  python3 rocko_claude_worker.py --no-easydoc  # solo Rocko, sin atender a EasyDoc

Variables: ROCKO_CLAUDE_MODEL, ROCKO_CLAUDE_EFFORT, ROCKO_CLAUDE_CONFIG_DIR (default ~/.claude-personal),
ROCKO_CLAUDE_STATE_DIR (default ~/.cache/rocko-claude; otro valor permite una 2ª instancia de prueba).
Credenciales: RECO_URL + RECO_TOKEN de ~/.codex/reminders-chirho/remote.env (las mismas del CLI).

Sin dependencias: solo stdlib.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

VERSION = '2'
REMOTE_ENV = Path.home() / '.codex' / 'reminders-chirho' / 'remote.env'
STATE_DIR = Path(os.environ.get('ROCKO_CLAUDE_STATE_DIR') or Path.home() / '.cache' / 'rocko-claude')   # carpeta vacía: claude -p corre acá, sin CLAUDE.md de ningún repo
PID_FILE = STATE_DIR / 'worker.pid'
LOG_FILE = STATE_DIR / 'worker.log'
DEFAULT_CONFIG_DIR = Path.home() / '.claude-personal'
WORK_CONFIG_DIR = Path.home() / '.claude'           # la instalación de TRABAJO: nunca se usa acá
POLL_SECONDS = 2.0
ERROR_BACKOFF = 10.0
PANE_CHECK_SECONDS = 15.0
USAGE_LIMIT_PAUSE = 30 * 60        # s que se le pide al servidor no mandarle nada tras agotar la cuota
USAGE_LIMIT_HINTS = ('usage limit', 'limit reached', 'rate limit', 'out of extra usage', 'hit your limit')

ROCKO_PREFIX = '/api/reco/rocko/'
EASYDOC_PREFIX = '/api/v1/easydoc/claude/laptop/'
EASYDOC_SLOTS = 2                  # documentos de EasyDoc a la vez
EASYDOC_RETRY_SECONDS = 60         # si el servidor no tiene (todavía) la cola de EasyDoc, no insistir cada 2 s
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
FLUSH_EVERY = 1.0                  # s entre trozos de texto hacia el servidor (el navegador sondea ~1 s)
HEARTBEAT_EVERY = 8.0              # s sin texto (Claude pensando) antes de un latido que renueva el plazo
SHUTDOWN_GRACE = 190               # s que se espera a un mensaje de Rocko en curso al cerrar

_stop = False
_children: set = set()             # procesos `claude` de EasyDoc vivos: se matan al cerrar
_children_lock = threading.Lock()


def _sleep(seconds: float) -> None:
    """Como time.sleep, pero se despierta enseguida con --stop (SIGTERM)."""
    end = time.monotonic() + seconds
    while not _stop and time.monotonic() < end:
        time.sleep(min(0.2, end - time.monotonic()))


def log(msg: str) -> None:
    print(f'{datetime.now():%Y-%m-%d %H:%M:%S} {msg}', flush=True)


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def load_remote_env() -> tuple[str, str]:
    env = {}
    if REMOTE_ENV.exists():
        for line in REMOTE_ENV.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip().removeprefix('export ').strip()] = v.strip().strip('"').strip("'")
    url = os.environ.get('RECO_URL') or env.get('RECO_URL', '')
    token = os.environ.get('RECO_TOKEN') or env.get('RECO_TOKEN', '')
    if not url or not token:
        sys.exit(f'Faltan RECO_URL / RECO_TOKEN (en {REMOTE_ENV}).')
    return url.rstrip('/'), token


def claude_config_dir() -> Path:
    path = Path(os.path.expanduser(os.environ.get('ROCKO_CLAUDE_CONFIG_DIR') or DEFAULT_CONFIG_DIR)).resolve()
    if path == WORK_CONFIG_DIR.resolve():
        sys.exit('ROCKO_CLAUDE_CONFIG_DIR apunta a ~/.claude (la instalación de trabajo). Rocko usa la cuenta '
                 'personal: ~/.claude-personal.')
    if not path.exists():
        sys.exit(f'No existe {path}: ¿iniciaste sesión con claudep?')
    return path


def claude_bin() -> str:
    found = shutil.which('claude') or str(Path.home() / '.local' / 'bin' / 'claude')
    if not Path(found).exists():
        sys.exit('No encuentro el ejecutable `claude`.')
    return found


# ---------------------------------------------------------------------------
# Servidor
# ---------------------------------------------------------------------------

class Server:
    def __init__(self, url: str, token: str):
        self.url, self.token = url, token

    def call(self, method: str, path: str, payload: dict | None = None, timeout: float = 20,
             prefix: str = ROCKO_PREFIX) -> tuple[int, dict]:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f'{self.url}{prefix}{path}', data=data, method=method, headers={
            'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — URL propia
                return resp.status, json.loads(resp.read() or b'{}')
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b'{}')
            except ValueError:
                body = {}
            return e.code, body


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def run_claude(job: dict, args, server: Server) -> tuple[bool, str, bool]:
    """(ok, texto o error, se_acabó_la_cuota)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    mcp = {'mcpServers': {'reco': {'type': 'http', 'url': f'{server.url}/api/reco/mcp/', 'headers': {
        'Authorization': f'Bearer {server.token}', 'X-Rocko-Job': str(job['id'])}}}}
    fd, mcp_path = tempfile.mkstemp(prefix='mcp-', suffix='.json', dir=STATE_DIR)   # 0600: lleva el token
    with os.fdopen(fd, 'w') as f:
        json.dump(mcp, f)
    content = [{'type': 'image', 'source': {'type': 'base64', 'media_type': i['mime'], 'data': i['data']}}
               for i in job.get('images') or []]
    content.append({'type': 'text', 'text': job['prompt']})
    message = {'type': 'user', 'message': {'role': 'user', 'content': content}}
    cmd = [claude_bin(), '-p', '--model', args.model, '--effort', args.effort,
           '--system-prompt', job['system'],
           '--tools', '',                       # nada de archivos ni terminal: solo las tools de reco
           '--mcp-config', mcp_path, '--strict-mcp-config', '--allowedTools', 'mcp__reco',
           '--setting-sources', '',             # sin hooks ni ajustes del usuario (ni CLAUDE.md)
           '--settings', json.dumps({'autoMemoryEnabled': False}),
           '--no-session-persistence',
           '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose']
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(claude_config_dir()))
    env.pop('ANTHROPIC_API_KEY', None)   # la suscripción, nunca una API key de pago
    try:
        proc = subprocess.run(cmd, input=json.dumps(message) + '\n', capture_output=True, text=True, env=env,
                              cwd=STATE_DIR, timeout=max(30, int(job.get('timeout') or 150)))
    except subprocess.TimeoutExpired:
        return False, 'claude tardó demasiado', False
    finally:
        os.unlink(mcp_path)
    result, tools = None, []
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'assistant':
            tools += [c.get('name', '?').removeprefix('mcp__reco__')
                      for c in event.get('message', {}).get('content', []) if c.get('type') == 'tool_use']
        elif event.get('type') == 'result':
            result = event
    if tools:
        log(f'  tools: {", ".join(tools)}')
    if result is None:
        detail = (proc.stderr or proc.stdout or f'código {proc.returncode}').strip()[-300:]
        return False, f'claude no devolvió resultado: {detail}', any(h in detail.lower() for h in USAGE_LIMIT_HINTS)
    text = str(result.get('result') or '').strip()
    if result.get('is_error') or result.get('subtype') != 'success' or not text:
        detail = text or json.dumps(result.get('errors') or result.get('subtype'))[:300]
        return False, detail, any(h in detail.lower() for h in USAGE_LIMIT_HINTS)
    return True, text, False


# ---------------------------------------------------------------------------
# EasyDoc: un documento, con el texto devuelto por trozos
# ---------------------------------------------------------------------------

def _spawn_easydoc(job: dict) -> subprocess.Popen:
    """`claude -p` sin tools ni MCP: recibe el pedido y escribe el documento, nada más."""
    model = str(job.get('model') or 'sonnet')
    effort = str(job.get('effort') or 'high')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,40}', model):
        model = 'sonnet'
    if effort not in EFFORTS:
        effort = 'high'
    blocks = []
    for f in job.get('files') or []:
        kind = 'document' if f.get('kind') == 'pdf' else 'image'
        blocks.append({'type': kind, 'source': {'type': 'base64', 'media_type': f['mime'], 'data': f['data']}})
    blocks.append({'type': 'text', 'text': job['prompt']})
    message = {'type': 'user', 'message': {'role': 'user', 'content': blocks}}
    cmd = [claude_bin(), '-p', '--model', model, '--effort', effort,
           '--system-prompt', job['system'],
           '--tools', '',                       # el documento es la única salida
           '--strict-mcp-config',               # y ningún MCP (ni de la cuenta ni de plugins)
           '--setting-sources', '',
           '--settings', json.dumps({'autoMemoryEnabled': False}),
           '--no-session-persistence',
           '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
           '--include-partial-messages']
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(claude_config_dir()))
    env.pop('ANTHROPIC_API_KEY', None)   # la suscripción, nunca una API key de pago
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=env, cwd=STATE_DIR, bufsize=1)
    with _children_lock:
        _children.add(proc)
    try:
        proc.stdin.write(json.dumps(message) + '\n')
        proc.stdin.close()
    except OSError:
        pass   # murió al arrancar: se ve en el código de salida
    return proc


def _pump(stream, sink: queue.Queue, tail: list | None = None) -> None:
    """Lee un pipe línea a línea sin bloquear el bucle principal (y sin llenar el pipe de stderr)."""
    for line in stream:
        if tail is not None:
            tail.append(line)
            del tail[:-40]
        else:
            sink.put(line)
    if tail is None:
        sink.put(None)   # fin del stdout


def run_easydoc(job: dict, server: Server) -> None:
    """Atiende un trabajo de EasyDoc de punta a punta; todo el reporte al servidor es de acá."""
    jid = job['id']
    call = lambda path, payload: server.call('POST', f'jobs/{jid}/{path}/', payload, timeout=20, prefix=EASYDOC_PREFIX)  # noqa: E731
    started = time.monotonic()
    deadline = started + max(30, int(job.get('timeout') or 600))
    log(f'EasyDoc {jid}: {job.get("model")}/{job.get("effort")}, {len(job["prompt"])} caracteres de pedido')
    text, sent, finish, result = '', 0, 'stop', None
    last_flush = time.monotonic()
    stderr_tail: list = []
    proc = None
    try:
        proc = _spawn_easydoc(job)
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=_pump, args=(proc.stdout, lines), daemon=True).start()
        threading.Thread(target=_pump, args=(proc.stderr, lines, stderr_tail), daemon=True).start()
        while True:
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                line = ''
            if line is None:
                break
            if line.strip():
                try:
                    event = json.loads(line)
                except ValueError:
                    event = {}
                if event.get('type') == 'stream_event':
                    inner = event.get('event') or {}
                    if inner.get('type') == 'content_block_delta' and (inner.get('delta') or {}).get('type') == 'text_delta':
                        text += inner['delta'].get('text', '')
                    elif inner.get('type') == 'message_delta' and (inner.get('delta') or {}).get('stop_reason') == 'max_tokens':
                        finish = 'length'
                elif event.get('type') == 'result':
                    result = event
            now = time.monotonic()
            if _stop:
                proc.kill()
                call('fail', {'error': 'La laptop se está cerrando.'})
                return
            if now > deadline:
                proc.kill()
                call('fail', {'error': 'claude tardó demasiado'})
                log(f'  EasyDoc {jid}: demasiado lento, cortado')
                return
            due = HEARTBEAT_EVERY if len(text) == sent else FLUSH_EVERY
            if now - last_flush >= due:
                last_flush = now
                try:
                    status, body = call('chunk', {'offset': sent, 'delta': text[sent:]})
                except (urllib.error.URLError, TimeoutError, OSError):
                    continue   # se reenvía en el próximo ciclo (el offset no avanzó)
                if status == 200 and body.get('cancel') or status in (404, 409):
                    proc.kill()
                    log(f'  EasyDoc {jid}: cancelado por el servidor (ya nadie espera el texto)')
                    return
                if status == 200:
                    sent = min(len(text), int(body.get('length', sent)))
        proc.wait(timeout=30)
    except Exception as e:  # noqa: BLE001 — lo que sea: que el navegador pase a Gemini
        if proc and proc.poll() is None:
            proc.kill()
        try:
            call('fail', {'error': f'{type(e).__name__}: {e}'})
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        log(f'  EasyDoc {jid}: falló ({type(e).__name__}: {e})')
        return
    finally:
        if proc:
            with _children_lock:
                _children.discard(proc)

    if result and result.get('stop_reason') == 'max_tokens':
        finish = 'length'
    final = text if text.strip() else str((result or {}).get('result') or '')
    took = time.monotonic() - started
    if result is None or result.get('is_error') or result.get('subtype') != 'success' or not final.strip():
        detail = (final or ''.join(stderr_tail) or f'código {proc.returncode}').strip()[-300:]
        limited = any(h in detail.lower() for h in USAGE_LIMIT_HINTS)
        try:
            call('fail', {'error': detail, 'pause_seconds': USAGE_LIMIT_PAUSE if limited else 0})
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        log(f'  EasyDoc {jid}: falló en {took:.0f} s ({detail[:160]})' + (' · cuota agotada: pausa' if limited else ''))
        return
    try:
        status, body = call('done', {'text': final, 'finish': finish})
        log(f'  EasyDoc {jid}: listo en {took:.0f} s ({len(final)} caracteres' + (', truncado' if finish == 'length' else '') + ')'
            if status == 200 else f'  EasyDoc {jid}: el servidor no aceptó el resultado ({status}); ya no era suyo')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f'  EasyDoc {jid}: no pude entregar el resultado ({e}); el navegador pasará a Gemini')


# ---------------------------------------------------------------------------
# Bucle
# ---------------------------------------------------------------------------

def pane_alive(pane: str) -> bool:
    res = subprocess.run(['tmux', 'display-message', '-p', '-t', pane, '#{pane_id}'], capture_output=True, text=True)
    return res.returncode == 0 and res.stdout.strip() == pane


def handle_rocko(job: dict, args, server: Server, engine: str) -> None:
    started = time.monotonic()
    log(f'mensaje {job["id"]}: {job["prompt"].rsplit("## Mensaje nuevo de Nelson", 1)[-1].strip()[:80]!r}')
    try:
        ok, text, limited = run_claude(job, args, server)
    except Exception as e:  # noqa: BLE001 — cualquier cosa rara: que conteste Gemini
        ok, text, limited = False, f'{type(e).__name__}: {e}', False
    took = time.monotonic() - started
    try:
        if ok:
            status, body = server.call('POST', f'jobs/{job["id"]}/done/', {'reply': text, 'engine': engine})
            log(f'  respondido en {took:.1f} s' if status == 200
                else f'  el servidor no aceptó la respuesta ({status}: {body.get("error")}); ya contestó otro')
        else:
            pause = USAGE_LIMIT_PAUSE if limited else 0
            server.call('POST', f'jobs/{job["id"]}/fail/', {'error': text, 'pause_seconds': pause})
            log(f'  falló en {took:.1f} s ({text[:200]}); contesta Gemini'
                + (f'. Cuota de Claude agotada: pausa de {pause // 60} min' if limited else ''))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f'  no pude avisarle al servidor ({e}); el mensaje volverá a la cola solo')


def serve(args) -> None:
    server = Server(*load_remote_env())
    claude_config_dir()   # falla temprano si la cuenta personal no está
    engine = f'claude:{args.model}/{args.effort}'
    info = {'model': args.model, 'effort': args.effort, 'host': socket.gethostname().split('.')[0],
            'version': VERSION}
    log(f'Rocko con Claude ({args.model}, esfuerzo {args.effort}) → {server.url}'
        + (' · EasyDoc: sí' if args.easydoc else ' · EasyDoc: no')
        + (f' · atado al panel {args.tie_pane}' if args.tie_pane else ''))
    last_pane_check = time.monotonic()
    easydoc_after = 0.0            # no preguntar por EasyDoc antes de este instante (servidor sin la cola, o sin red)
    easydoc_warned = False
    threads: list[threading.Thread] = []
    easydoc_running = threading.Semaphore(args.easydoc_slots)

    def spawn(target, *a, name: str, daemon: bool = False) -> threading.Thread:
        t = threading.Thread(target=target, args=a, name=name, daemon=daemon)
        t.start()
        threads[:] = [x for x in threads if x.is_alive()] + [t]
        return t

    def easydoc_job(job: dict) -> None:
        try:
            run_easydoc(job, server)
        finally:
            easydoc_running.release()

    try:
        while not _stop:
            if args.tie_pane and time.monotonic() - last_pane_check > PANE_CHECK_SECONDS:
                last_pane_check = time.monotonic()
                if not pane_alive(args.tie_pane):
                    log('Se cerró el proyecto reco (su panel de tmux ya no existe): salgo. Rocko vuelve a Gemini.')
                    return
            # Rocko: esta pregunta es también el latido de "la laptop está viva" (para Rocko y para EasyDoc).
            try:
                status, body = server.call('POST', 'claim/', info)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                log(f'sin conexión con el servidor ({e}); reintento en {ERROR_BACKOFF:.0f} s')
                _sleep(ERROR_BACKOFF)
                continue
            if status != 200:
                log(f'el servidor respondió {status}: {body}; reintento en {ERROR_BACKOFF:.0f} s')
                _sleep(ERROR_BACKOFF)
                continue
            worked = False
            job = body.get('job')
            if job:
                worked = True
                t = spawn(handle_rocko, job, args, server, engine, name=f'rocko-{job["id"]}')
                if args.once:
                    t.join()
                    return
            if args.easydoc and time.monotonic() >= easydoc_after and easydoc_running.acquire(blocking=False):
                handed_over = False
                try:
                    status, body = server.call('POST', 'claim/', {}, prefix=EASYDOC_PREFIX)
                    if status != 200:
                        if not easydoc_warned:
                            log(f'EasyDoc: el servidor respondió {status} (¿aún sin desplegar?); sigo con Rocko y '
                                f'pregunto de nuevo cada {EASYDOC_RETRY_SECONDS} s')
                            easydoc_warned = True
                        easydoc_after = time.monotonic() + EASYDOC_RETRY_SECONDS
                    elif body.get('job'):
                        easydoc_warned = False
                        worked = handed_over = True
                        spawn(easydoc_job, body['job'], name=f'easydoc-{body["job"]["id"]}')
                except (urllib.error.URLError, TimeoutError, OSError):
                    pass   # la red se vio arriba con la pregunta de Rocko; acá solo se reintenta
                finally:
                    if not handed_over:
                        easydoc_running.release()
            if not worked:
                if args.once:
                    return
                _sleep(POLL_SECONDS)
    finally:
        # Cerrar: EasyDoc se corta ya (el navegador pasa a Gemini), un mensaje de Rocko en curso se deja terminar.
        with _children_lock:
            for proc in list(_children):
                proc.kill()
        for t in threads:
            t.join(timeout=SHUTDOWN_GRACE if t.name.startswith('rocko') else 10)


# ---------------------------------------------------------------------------
# Instancia única, segundo plano y estado
# ---------------------------------------------------------------------------

def _running_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(STATE_DIR / 'worker.lock', 'w')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    PID_FILE.write_text(str(os.getpid()))
    return handle


def start_background(args) -> None:
    if _running_pid():
        print(f'🦘 Rocko con Claude ya está corriendo (pid {_running_pid()}). Log: {LOG_FILE}')
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, os.path.abspath(__file__), '--model', args.model, '--effort', args.effort,
           '--easydoc-slots', str(args.easydoc_slots)]
    if not args.easydoc:
        cmd.append('--no-easydoc')
    pane = args.tie_pane or os.environ.get('TMUX_PANE')
    if pane:
        cmd += ['--tie-pane', pane]
    with open(LOG_FILE, 'a') as out:
        subprocess.Popen(cmd, stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True)
    print(f'🦘 Rocko con Claude ({args.model}, esfuerzo {args.effort}) en segundo plano'
          + (' mientras el proyecto reco esté abierto' if pane else '') + f'. Log: {LOG_FILE}')


def show_status() -> None:
    pid = _running_pid()
    print(f'Proceso: {"corriendo (pid %s)" % pid if pid else "detenido"} · log: {LOG_FILE}')
    try:
        status, body = Server(*load_remote_env()).call('GET', 'status/')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f'Servidor: sin conexión ({e})')
        return
    print(f'Servidor ({status}): ' + json.dumps(body, ensure_ascii=False, indent=1))
    try:
        status, body = Server(*load_remote_env()).call('GET', 'status/', prefix=EASYDOC_PREFIX)
    except (urllib.error.URLError, TimeoutError, OSError):
        return
    print(f'EasyDoc ({status}): ' + (json.dumps(body, ensure_ascii=False) if status == 200 else 'el servidor aún no tiene la cola de EasyDoc'))


def main() -> None:
    p = argparse.ArgumentParser(description='Rocko (bot de Telegram de reco) contestando con Claude.')
    p.add_argument('--model', default=os.environ.get('ROCKO_CLAUDE_MODEL', 'sonnet'))
    p.add_argument('--effort', default=os.environ.get('ROCKO_CLAUDE_EFFORT', 'low'),
                   choices=('low', 'medium', 'high', 'xhigh', 'max'))
    p.add_argument('--no-easydoc', dest='easydoc', action='store_false',
                   help='No atender los documentos de EasyDoc (solo Rocko).')
    p.add_argument('--easydoc-slots', type=int, default=EASYDOC_SLOTS,
                   help=f'Documentos de EasyDoc a la vez (default {EASYDOC_SLOTS}).')
    p.add_argument('--background', action='store_true', help='Correr en segundo plano (atado al panel de tmux).')
    p.add_argument('--tie-pane', default='', help='Salir cuando se cierre este panel de tmux (p. ej. %%3).')
    p.add_argument('--status', action='store_true')
    p.add_argument('--stop', action='store_true')
    p.add_argument('--once', action='store_true', help='Atender a lo sumo un mensaje y salir (pruebas).')
    args = p.parse_args()
    args.easydoc_slots = max(1, min(args.easydoc_slots, 4))

    if args.status:
        return show_status()
    if args.stop:
        pid = _running_pid()
        if pid:
            os.kill(pid, signal.SIGTERM)
            print(f'Detenido (pid {pid}). En ~15 s Rocko vuelve a Gemini.')
        else:
            print('No estaba corriendo.')
        return
    if args.background:
        return start_background(args)

    lock = _lock()
    if lock is None:
        sys.exit(f'Ya hay otro rocko_claude_worker corriendo (pid {_running_pid()}).')

    def _bye(*_):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGHUP, _bye)
    try:
        serve(args)
    except KeyboardInterrupt:
        pass
    finally:
        PID_FILE.unlink(missing_ok=True)
        log('Detenido.')


if __name__ == '__main__':
    main()
