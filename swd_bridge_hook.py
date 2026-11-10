#!/usr/bin/env python3
"""Hook de Claude Code / Codex: anota en qué está el agente de ESTE panel de tmux, para que swd-bridge lo muestre.

    swd_bridge_hook.py <claude|codex> <Evento>       (el JSON del hook llega por stdin; se lee si viene)

Escribe ~/.config/swd-bridge/state/<pane>.json → {state, agent, event, ts}. Estados:
    working      el agente está trabajando (mandaste algo, o acaba de usar una herramienta)
    needs_input  espera tu permiso o una respuesta
    done         terminó su turno: te toca a ti
    error        su turno terminó con error
SessionEnd borra el archivo. Fuera de tmux no hace nada.

⚠️ No imprime NADA: en algunos eventos (UserPromptSubmit) lo que un hook escribe en stdout se agrega al contexto
del agente. Siempre sale con 0 y rápido: un hook lento o con error estorbaría al agente.
"""
import json
import os
import sys
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get('SWD_BRIDGE_DIR') or Path.home() / '.config' / 'swd-bridge') / 'state'
EVENTS = {
    'SessionStart': 'done',
    'UserPromptSubmit': 'working',
    'PostToolUse': 'working',
    'PermissionRequest': 'needs_input',
    'Notification': 'needs_input',
    'Stop': 'done',
    'Interrupt': 'done',
    'StopFailure': 'error',
}


def main() -> None:
    pane = os.environ.get('TMUX_PANE', '')
    if not pane.startswith('%'):
        return
    agent = sys.argv[1] if len(sys.argv) > 1 else 'agent'
    event = sys.argv[2] if len(sys.argv) > 2 else ''
    data = {}
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read(65536)
            data = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        data = {}
    event = event or data.get('hook_event_name', '')
    path = STATE_DIR / f'{pane.lstrip("%")}.json'
    if event == 'SessionEnd':
        path.unlink(missing_ok=True)
        return
    state = EVENTS.get(event)
    if event == 'Notification' and data.get('notification_type') == 'idle_prompt':
        state = 'done'        # «sigo esperando tu mensaje»: no pide permiso, solo te toca a ti
    if not state:
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps({'state': state, 'agent': agent, 'event': event, 'ts': time.time()}))
    tmp.replace(path)


if __name__ == '__main__':
    try:
        main()
    except Exception:  # noqa: BLE001 — un hook nunca debe romper al agente
        pass
    sys.exit(0)
