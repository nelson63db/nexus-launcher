#!/usr/bin/env python3
"""Registro único de puertos y perfiles de agente para el launcher (nexus-launcher.py).

Una sola fuente de verdad: ~/.config/swd-bridge/registry.json
  - ports:    alias de proyecto → puerto. Los «fijos» ya estaban escritos en las configuraciones de los
              proyectos (CSRF, CORS, proxies, READMEs) y no se mueven; el resto se asigna UNA vez en
              8100–8199 y no se reutiliza nunca.
  - profiles: nombre → {provider, family|model, effort}. Se mezclan sobre los de fábrica.

Los modelos NO se copian a mano: los de Codex salen de ~/.codex/models_cache.json (los que su CLI
muestra), los de Claude y sus esfuerzos de `claude --help`. Un perfil guarda la FAMILIA («luna»,
«terra», «sol») y se resuelve a la versión más nueva al lanzar.

CLI:  swd_registry.py port <alias> | ports | profiles | check <alias> | catalog
Solo stdlib (como el resto del launcher).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REG_PATH = Path(os.environ.get('SWD_REGISTRY', Path.home() / '.config' / 'swd-bridge' / 'registry.json'))
CODEX_CACHE = Path.home() / '.codex' / 'models_cache.json'
AUTO_RANGE = (8100, 8199)

# Ya distintos entre sí y escritos en configuraciones de los proyectos: se respetan tal cual.
PINNED_PORTS = {
    'safety': 8000,        # CSRF_TRUSTED_ORIGINS de su settings (repo de trabajo: no se toca)
    'cear': 8001,
    'luna': 8002,
    'egradebook': 8004,
    'AccessCore': 8005,    # README y start_dev_chirho.sh
    'swd': 8006,           # CSRF/CORS, proxy de EasyDoc y dev.sh; sus vites: 5174/5175/5177 fijos
    'intranet': 5173,      # README/AGENTS del proyecto
    'ia-mentor': 9003,     # `next dev --port 9003` en su package.json
    'inmtec': 8080,
    'artemis': 8008,       # ARTEMIS_UI_PORT por defecto
    'rebar': 8081,         # Expo (bundler)
    'n8n-workflows': 5679,
    'vc-saves-backup': 8123,
}

# (etiqueta, proveedor, familia/modelo, esfuerzo). Claude: alias del CLI; Codex: familia.
DEFAULT_PROFILES = {
    'sonnet-bajo':  {'provider': 'claude', 'model': 'sonnet', 'effort': 'low'},
    'sonnet-medio': {'provider': 'claude', 'model': 'sonnet', 'effort': 'medium'},
    'sonnet-alto':  {'provider': 'claude', 'model': 'sonnet', 'effort': 'high'},
    'opus-alto':    {'provider': 'claude', 'model': 'opus', 'effort': 'high'},
    'luna-bajo':    {'provider': 'codex', 'family': 'luna', 'effort': 'low'},
    'terra-medio':  {'provider': 'codex', 'family': 'terra', 'effort': 'medium'},
    'sol-alto':     {'provider': 'codex', 'family': 'sol', 'effort': 'high'},
}
CLAUDE_MODELS_FALLBACK = ['fable', 'opus', 'sonnet', 'haiku']
CLAUDE_EFFORTS_FALLBACK = ['low', 'medium', 'high', 'xhigh', 'max']
WORK_ROOT = Path.home() / 'dev-chirho'


# ---------------------------------------------------------------------------
# Archivo
# ---------------------------------------------------------------------------

def load() -> dict:
    try:
        data = json.loads(REG_PATH.read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault('ports', {})
    data.setdefault('profiles', {})
    return data


def save(data: dict) -> None:
    REG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REG_PATH.with_suffix(f'.{os.getpid()}.tmp')      # propio de cada proceso: dos launchers a la vez no se pisan
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + '\n')
    tmp.replace(REG_PATH)


# ---------------------------------------------------------------------------
# Puertos
# ---------------------------------------------------------------------------

def port_for(alias: str) -> int:
    """El puerto de un proyecto. Si no tiene, se le asigna el siguiente libre del rango y queda guardado."""
    REG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REG_PATH.with_suffix('.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)           # leer-asignar-guardar es una sola operación: dos alias no reciben el mismo puerto
        data = load()
        ports = data['ports']
        if alias in ports:
            return int(ports[alias])
        if alias in PINNED_PORTS:
            ports[alias] = PINNED_PORTS[alias]
        else:
            taken = {int(p) for p in ports.values()} | set(PINNED_PORTS.values())
            free = next((p for p in range(AUTO_RANGE[0], AUTO_RANGE[1] + 1) if p not in taken), None)
            if free is None:
                raise RuntimeError('No quedan puertos libres en el rango 8100–8199.')
            ports[alias] = free
        save(data)
        return int(ports[alias])


def listening(port: int) -> dict | None:
    """Quién escucha en ese puerto (pid, comando, carpeta) o None si está libre."""
    try:
        out = subprocess.run(['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-Fpc'],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    pid = re.search(r'^p(\d+)', out, re.M)
    if not pid:
        return None
    cmd = re.search(r'^c(.+)$', out, re.M)
    cwd = ''
    try:
        cwd = re.search(r'^n(.+)$', subprocess.run(
            ['lsof', '-a', '-p', pid.group(1), '-d', 'cwd', '-Fn'], capture_output=True, text=True,
            timeout=5).stdout, re.M).group(1)
    except (AttributeError, OSError, subprocess.TimeoutExpired):
        pass
    return {'pid': int(pid.group(1)), 'command': cmd.group(1) if cmd else '?', 'cwd': cwd}


# ---------------------------------------------------------------------------
# Catálogo de agentes
# ---------------------------------------------------------------------------

def claude_catalog() -> dict:
    """Modelos (alias) y esfuerzos que el CLI de Claude dice aceptar; si falla, una lista de respaldo."""
    models, efforts = list(CLAUDE_MODELS_FALLBACK), list(CLAUDE_EFFORTS_FALLBACK)
    try:
        text = subprocess.run(['claude', '--help'], capture_output=True, text=True, timeout=15).stdout
        flat = re.sub(r'\s+', ' ', text)
        m = re.search(r'--effort <level> .*?\(([a-z, ]+)\)', flat)
        if m:
            efforts = [e.strip() for e in m.group(1).split(',') if e.strip()]
        m = re.search(r"alias for the latest model \(e\.g\. ([^)]+)\)", flat)
        if m:
            found = re.findall(r"'([a-z0-9.-]+)'", m.group(1))
            models = found + [x for x in CLAUDE_MODELS_FALLBACK if x not in found]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {'models': models, 'efforts': efforts}


def _slug_key(slug: str):
    m = re.match(r'gpt-([\d.]+)-([a-z]+)', slug)
    return (tuple(int(x) for x in m.group(1).split('.') if x.isdigit()), m.group(2)) if m else ((), slug)


def codex_catalog() -> list[dict]:
    """Los modelos que el CLI de Codex muestra (visibility «list»), con sus esfuerzos."""
    try:
        data = json.loads(CODEX_CACHE.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for m in data.get('models', []):
        if m.get('visibility') != 'list' or not m.get('slug'):
            continue
        out.append({'slug': m['slug'], 'name': m.get('display_name') or m['slug'],
                    'efforts': [r.get('effort') for r in m.get('supported_reasoning_levels', []) if r.get('effort')],
                    'default': m.get('default_reasoning_level')})
    return sorted(out, key=lambda m: _slug_key(m['slug']), reverse=True)


def resolve_codex_family(family: str) -> str | None:
    """«luna» → la versión más nueva de esa familia que el CLI ofrece hoy (p. ej. gpt-6-luna)."""
    cands = [m['slug'] for m in codex_catalog() if m['slug'].endswith('-' + family)]
    return cands[0] if cands else None


def profiles() -> dict:
    merged = {k: dict(v) for k, v in DEFAULT_PROFILES.items()}
    merged.update(load().get('profiles', {}))
    return merged


def claude_account_alias(project_path: str) -> str:
    """Proyectos de trabajo (~/dev-chirho) usan la instalación de trabajo (`claudet`); el resto, la personal."""
    p = Path(os.path.expanduser(project_path)).resolve()
    return 'claudet' if WORK_ROOT in (p, *p.parents) else 'claudep'


def agent_command(spec: dict, project_path: str = '~') -> str:
    """El comando que se escribe en el panel. Usa los alias de .zshrc (claudep/claudet/codext) para no
    duplicar sus banderas: si cambias un alias, el launcher lo sigue."""
    provider, effort = spec['provider'], spec.get('effort')
    if provider == 'claude':
        cmd = f"{claude_account_alias(project_path)} --model {spec['model']}"
        return cmd + (f' --effort {effort}' if effort else '')
    model = spec.get('model') or resolve_codex_family(spec.get('family', ''))
    if not model:
        raise ValueError(f"Codex no ofrece la familia «{spec.get('family')}» en este momento.")
    cmd = f'codext -m {model}'
    return cmd + (f' -c model_reasoning_effort={effort}' if effort else '')


def describe(spec: dict) -> str:
    who = 'Claude' if spec['provider'] == 'claude' else 'Codex'
    model = spec.get('model') or resolve_codex_family(spec.get('family', '')) or spec.get('family')
    return f"{who} · {model} · esfuerzo {spec.get('effort') or 'por defecto'}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else 'ports'
    if cmd == 'port' and len(argv) > 2:
        print(port_for(argv[2]))
    elif cmd == 'ports':
        for alias, port in sorted(load()['ports'].items(), key=lambda kv: int(kv[1])):
            print(f'{port:>5}  {alias}')
    elif cmd == 'profiles':
        for name, spec in profiles().items():
            print(f'{name:<14} {describe(spec)}')
    elif cmd == 'check' and len(argv) > 2:
        port = port_for(argv[2])
        owner = listening(port)
        print(f'{argv[2]}: puerto {port} ' + (f"OCUPADO por {owner['command']} (pid {owner['pid']}, {owner['cwd']})"
                                              if owner else 'libre'))
        return 1 if owner else 0
    elif cmd == 'catalog':
        c = claude_catalog()
        print('Claude  modelos:', ', '.join(c['models']), '| esfuerzos:', ', '.join(c['efforts']))
        for m in codex_catalog():
            print(f"Codex   {m['slug']:<16} ({m['name']}) esfuerzos: {', '.join(m['efforts'])}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
