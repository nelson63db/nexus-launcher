"""
reco_tools.py — herramientas Needle para administrar codex-reminder-chirho ("reco")
desde instrucciones en lenguaje natural.

Uso rápido:

    from reco_tools import build_agent

    agent = build_agent()
    resultado = agent.run(
        "crea una tarea para safetyrecordchirho que diga: revisar el fix del dropdown"
    )
    print(resultado["results"])

También se puede usar cada función suelta (son funciones Python normales,
decoradas con @needle.tool) sin pasar por el agente, por ejemplo desde un
hook o un script:

    from reco_tools import crear_tarea
    crear_tarea(repo="Nostr_Music_Project", titulo="Migrar a nueva API de relay")

Resolución de proyecto
-----------------------
Dado un nombre de repo (exacto, parcial o con guiones/mayúsculas distintas),
se busca la carpeta real en:

  - ~/PycharmProjects/*      -> personal  -> project = "personal/<Carpeta>"
  - ~/napps/*                -> personal  -> project = "personal/<Carpeta>"
  - ~/dev-chirho/*           -> trabajo   -> project = código según tabla de
                                             ~/.claude/CLAUDE.md (SR, INT, TM, ...)
                                             o "OTH" si no matchea ninguna keyword.

Si el repo no se encuentra en ninguna raíz, las funciones devuelven un error
con sugerencias en vez de crear la tarea a ciegas.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Literal, Optional

import needle

# ---------------------------------------------------------------------------
# Config / rutas
# ---------------------------------------------------------------------------

HOME = Path.home()
RECO_BIN = str(HOME / ".local" / "bin" / "codex-reminder-chirho")

PERSONAL_ROOTS = [HOME / "PycharmProjects", HOME / "napps"]
WORK_ROOT = HOME / "dev-chirho"

# Copiado de la tabla de detección de proyecto en ~/.claude/CLAUDE.md
# (Equipo DIE / Chirho Nexus). No modificar ese archivo: si la tabla cambia
# ahí, actualizar esta copia a mano.
WORK_PROJECT_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("safetyrecord", "safety-record", "safety_ai"), "SR"),
    (("safety-inspect",), "SI"),
    (("safety-control",), "SCS"),
    (("intranet",), "INT"),
    (("task-management", "task-data", "taskboard"), "TM"),
    (("cloudflare",), "CFS"),
    (("monitor",), "MON"),
    (("clock",), "CLK"),
    (("evolution",), "EVO"),
    (("access-core",), "AC"),
    (("rebar-core",), "RB"),
    (("dies_hub", "dies-hub"), "DH"),
    (("voiceguard", "whisperflow"), "VG"),
    (("iamentor", "ia-mentor"), "IM"),
    (("nexus-chirho", "chirho-nexus"), "NEX"),
]

TASKS_PATH = HOME / ".codex" / "reminders-chirho" / "tasks.json"


# ---------------------------------------------------------------------------
# Resolución de repo -> scope/proyecto
# ---------------------------------------------------------------------------


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _list_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]


def _work_project_code(folder_name: str) -> str:
    lowered = folder_name.lower()
    for keywords, code in WORK_PROJECT_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return code
    return "OTH"


class RepoNotFoundError(Exception):
    def __init__(self, query: str, suggestions: list[str]):
        self.query = query
        self.suggestions = suggestions
        msg = f"No encontré ningún repo que coincida con '{query}'."
        if suggestions:
            msg += " ¿Quisiste decir: " + ", ".join(suggestions) + "?"
        super().__init__(msg)


def resolve_project(repo: str) -> dict:
    """Encuentra la carpeta real de `repo` bajo ~/PycharmProjects, ~/napps o
    ~/dev-chirho y devuelve el scope (personal/trabajo) y el valor de
    `--project` que hay que pasarle a codex-reminder-chirho.

    Lanza RepoNotFoundError si no hay match, con sugerencias por similitud.
    """
    query_norm = _normalize(repo)
    if not query_norm:
        raise RepoNotFoundError(repo, [])

    candidates: list[tuple[Path, str]] = []  # (path, scope)
    for root in PERSONAL_ROOTS:
        candidates += [(p, "personal") for p in _list_dirs(root)]
    candidates += [(p, "trabajo") for p in _list_dirs(WORK_ROOT)]

    # 1) match exacto (case-insensitive)
    for path, scope in candidates:
        if path.name.lower() == repo.lower():
            return _build_resolution(path, scope)

    # 2) match por substring normalizado (en cualquier dirección)
    substring_matches = [
        (path, scope)
        for path, scope in candidates
        if query_norm in _normalize(path.name) or _normalize(path.name) in query_norm
    ]
    if len(substring_matches) == 1:
        path, scope = substring_matches[0]
        return _build_resolution(path, scope)
    if len(substring_matches) > 1:
        names = sorted({p.name for p, _ in substring_matches})
        raise RepoNotFoundError(repo, names)

    # 3) sin match directo: sugerir por similitud
    all_names = [p.name for p, _ in candidates]
    suggestions = difflib.get_close_matches(repo, all_names, n=5, cutoff=0.5)
    raise RepoNotFoundError(repo, suggestions)


def _build_resolution(path: Path, scope: str) -> dict:
    if scope == "personal":
        project = f"personal/{path.name}"
    else:
        project = _work_project_code(path.name)
    return {"scope": scope, "project": project, "path": str(path), "repo": path.name}


# ---------------------------------------------------------------------------
# Ejecución del CLI
# ---------------------------------------------------------------------------


class RecoCommandError(Exception):
    pass


def _run_reco(args: list[str]) -> str:
    result = subprocess.run(
        [RECO_BIN, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RecoCommandError((result.stderr or result.stdout).strip() or f"codex-reminder-chirho {' '.join(args)} falló")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Tools Needle — todo lo que expone la CLI de codex-reminder-chirho
# ---------------------------------------------------------------------------


@needle.tool
def crear_tarea(
    repo: str,
    titulo: str,
    detalles: str = "",
    vencimiento: Optional[str] = None,
    prioridad: Literal["high", "medium", "low"] = "medium",
):
    """Crea una tarea/recordatorio nuevo en reco (codex-reminder-chirho) para un repo.

    Args:
        repo: nombre (exacto o parcial) del repositorio/proyecto, ej. "safetyrecordchirho" o "Nostr_Music_Project"
        titulo: título breve de la tarea
        detalles: contexto o descripción adicional, opcional
        vencimiento: fecha límite YYYY-MM-DD, "hoy" o "mañana", opcional
        prioridad: high, medium o low
    """
    resolved = resolve_project(repo)
    args = ["add", titulo, "--project", resolved["project"], "--priority", prioridad]
    if detalles:
        args += ["--details", detalles]
    if vencimiento:
        args += ["--due", vencimiento]
    salida = _run_reco(args)
    return {"repo_resuelto": resolved, "salida": salida}


@needle.tool
def listar_tareas(todas: bool = False):
    """Lista las tareas de reco.

    Args:
        todas: si es True incluye también las tareas hechas/canceladas; por defecto solo lista las abiertas
    """
    args = ["list"]
    if todas:
        args.append("--all")
    return {"salida": _run_reco(args)}


@needle.tool
def marcar_hecha(id: str):
    """Marca una tarea de reco como hecha (done).

    Args:
        id: identificador de la tarea, ej. R0012
    """
    return {"salida": _run_reco(["done", id])}


@needle.tool
def cambiar_estado(
    id: str,
    estado: Literal["pending", "in_progress", "review", "done", "blocked", "cancelled"],
):
    """Cambia el estado de una tarea de reco.

    Args:
        id: identificador de la tarea, ej. R0012
        estado: nuevo estado
    """
    return {"salida": _run_reco(["status", id, estado])}


@needle.tool
def cambiar_prioridad(id: str, prioridad: Literal["high", "medium", "low"]):
    """Cambia la prioridad de una tarea de reco.

    Args:
        id: identificador de la tarea, ej. R0012
        prioridad: high, medium o low
    """
    return {"salida": _run_reco(["priority", id, prioridad])}


@needle.tool
def cambiar_vencimiento(id: str, vencimiento: str):
    """Cambia la fecha de vencimiento de una tarea de reco.

    Args:
        id: identificador de la tarea, ej. R0012
        vencimiento: YYYY-MM-DD, "hoy" o "mañana"
    """
    return {"salida": _run_reco(["due", id, vencimiento])}


@needle.tool
def enfocar_tarea(id: str):
    """Marca una tarea de reco como la tarea activa/enfocada ahora mismo.

    Args:
        id: identificador de la tarea, ej. R0012
    """
    return {"salida": _run_reco(["focus", id])}


@needle.tool
def quitar_foco():
    """Quita el foco de la tarea activa en reco, sin marcarla como hecha."""
    return {"salida": _run_reco(["focus", "--clear"])}


@needle.tool
def vincular_sesion(id: str, sesion: Optional[str] = None):
    """Vincula una sesión de Codex/Claude a una tarea de reco.

    Args:
        id: identificador de la tarea, ej. R0012
        sesion: id de la sesión a vincular; si se omite usa la sesión más reciente
    """
    args = ["link", id]
    if sesion:
        args += ["--session", sesion]
    return {"salida": _run_reco(args)}


@needle.tool
def sincronizar_habitica(cuenta: Literal["trabajo", "personal", "all"] = "all"):
    """Sincroniza las tareas de reco con Habitica.

    Args:
        cuenta: qué cuenta de Habitica sincronizar
    """
    return {"salida": _run_reco(["habitica-sync", "--account", cuenta])}


@needle.tool
def sincronizar_sesiones():
    """Sincroniza el worklog de reco con las sesiones de Codex/Claude registradas."""
    return {"salida": _run_reco(["sync-sessions"])}


@needle.tool
def ver_estadisticas():
    """Muestra estadísticas de reco: sesiones, tiempo activo y tokens por modelo."""
    return {"salida": _run_reco(["stats"])}


@needle.tool
def ver_vencidas():
    """Lista las tareas de reco vencidas o que vencen hoy."""
    return {"salida": _run_reco(["due"])}


@needle.tool
def editar_tarea(
    id: str,
    titulo: Optional[str] = None,
    detalles: Optional[str] = None,
    repo: Optional[str] = None,
):
    """Edita el título, los detalles o el repo/proyecto de una tarea existente.

    La CLI de reco no expone edición de estos campos (solo status/due/priority),
    así que esto edita directamente el store en ~/.codex/reminders-chirho/tasks.json.
    No dispara sincronización con Habitica.

    Args:
        id: identificador de la tarea, ej. R0012
        titulo: nuevo título, opcional
        detalles: nuevos detalles, opcional
        repo: repo/proyecto nuevo (se resuelve igual que en crear_tarea), opcional
    """
    if not TASKS_PATH.is_file():
        raise RecoCommandError(f"No existe el store de tareas: {TASKS_PATH}")
    data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    tasks = data.get("tasks_chirho", [])
    task = next((t for t in tasks if t.get("id_chirho") == id), None)
    if task is None:
        raise RecoCommandError(f"No existe la tarea {id}")

    resolved = None
    if titulo is not None:
        task["title_chirho"] = titulo
    if detalles is not None:
        task["details_chirho"] = detalles
    if repo is not None:
        resolved = resolve_project(repo)
        task["project_chirho"] = resolved["project"]

    if titulo is None and detalles is None and repo is None:
        raise RecoCommandError("Nada que editar: pasá titulo, detalles y/o repo.")

    from datetime import datetime, timezone

    task["updated_at_chirho"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    TASKS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"tarea": task, "repo_resuelto": resolved}


ALL_TOOLS = [
    crear_tarea,
    listar_tareas,
    marcar_hecha,
    cambiar_estado,
    cambiar_prioridad,
    cambiar_vencimiento,
    enfocar_tarea,
    quitar_foco,
    vincular_sesion,
    sincronizar_habitica,
    sincronizar_sesiones,
    ver_estadisticas,
    ver_vencidas,
    editar_tarea,
]


def build_agent(**kwargs) -> "needle.Needle":
    """Construye un agente Needle con todas las tools de reco cargadas."""
    return needle.Needle(tools=ALL_TOOLS, **kwargs)


# ---------------------------------------------------------------------------
# Propuesta + confirmación (en vez de ejecución ciega)
# ---------------------------------------------------------------------------
#
# Needle 2 es un modelo de 45M de parámetros: con instrucciones largas o poco
# estructuradas en español a veces elige la tool equivocada o inventa un
# argumento (lo hemos visto: "confidence" no es un predictor confiable de
# acierto por sí solo -- llamadas correctas también salen con confidence
# ~0.0 cuando algún campo opcional queda mal poblado). Por eso acá NUNCA se
# ejecuta una tool directo desde `agent.run()`; primero se muestra qué
# llamaría el modelo y se pide confirmación explícita.

_TOOLS_BY_NAME = {tool.__name__: tool for tool in ALL_TOOLS}

CONFIDENCE_WARN_THRESHOLD = 0.5

C_DIM = "\033[2m"
C_RESET = "\033[0m"


def _describe_call(call: dict) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in (call.get("arguments") or {}).items())
    return f"{call.get('name')}({args})"


def _propose(agent: "needle.Needle", instruccion: str) -> dict:
    """Le pide al modelo qué llamaría, sin ejecutar nada todavía."""
    agent.reset()
    return agent.complete(instruccion)


def _execute_calls(calls: list[dict]) -> list[dict]:
    """Ejecuta las tools reales ya confirmadas por el usuario."""
    resultados = []
    for call in calls:
        nombre = call.get("name")
        fn = _TOOLS_BY_NAME.get(nombre)
        if fn is None:
            resultados.append({"tool": nombre, "error": f"tool desconocida: {nombre}"})
            continue
        try:
            resultados.append({"tool": nombre, "resultado": fn(**(call.get("arguments") or {}))})
        except (RepoNotFoundError, RecoCommandError) as error:
            resultados.append({"tool": nombre, "error": str(error)})
        except TypeError as error:
            # argumentos que no matchean la firma real de la tool
            resultados.append({"tool": nombre, "error": f"argumentos inválidos: {error}"})
    return resultados


def _print_resultados(resultados: list[dict]) -> None:
    for r in resultados:
        if "error" in r:
            print(f"  ✖ {r['tool']}: {r['error']}")
        else:
            print(f"  ✔ {r['tool']}: {json.dumps(r['resultado'], ensure_ascii=False)}")


def procesar_instruccion(agent: "needle.Needle", instruccion: str, auto_confirmar: bool = False) -> None:
    """Propone, muestra y (si se confirma) ejecuta una instrucción en lenguaje natural."""
    propuesta = _propose(agent, instruccion)
    confianza = propuesta.get("confidence") or 0.0

    if propuesta.get("type") != "call" or not propuesta.get("function_calls"):
        razon = propuesta.get("reasoning") or "no encontré ninguna acción para eso."
        print(f"(sin acción) {razon}")
        return

    calls = propuesta["function_calls"]
    print(f"Propuesta (confianza {confianza:.2f}):")
    for call in calls:
        print(f"  - {_describe_call(call)}")
    if propuesta.get("reasoning"):
        print(f"  {C_DIM}razón: {propuesta['reasoning']}{C_RESET}")
    if confianza < CONFIDENCE_WARN_THRESHOLD:
        print("  ⚠ confianza baja: revisá bien los argumentos antes de confirmar.")

    if auto_confirmar:
        confirmado = True
    else:
        respuesta = input("¿Ejecutar? [s/N]: ").strip().lower()
        confirmado = respuesta in ("s", "si", "sí", "y", "yes")

    if not confirmado:
        print("cancelado, no se ejecutó nada.")
        return

    _print_resultados(_execute_calls(calls))


def _repl() -> None:
    """Modo interactivo: quedar esperando instrucciones en la terminal."""
    print("=== reco — administrador de tareas por lenguaje natural (Needle) ===")
    print('Ej: crea una tarea para el repo intranet que diga: proceso de facturacion')
    print("Cada instrucción se propone primero y pide confirmación antes de tocar reco.")
    print("Ctrl+D o 'salir' para terminar.\n")

    agent = build_agent()
    while True:
        try:
            instruccion = input("reco> ").strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            continue

        if not instruccion:
            continue
        if instruccion.lower() in ("salir", "exit", "quit"):
            break

        try:
            procesar_instruccion(agent, instruccion)
        except Exception as error:  # último recurso: no tirar abajo el REPL
            print(f"⚠ error inesperado: {error}")
        print()

    print("Hasta luego.")


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    auto_yes = "--yes" in argv or "-y" in argv
    argv = [a for a in argv if a not in ("--yes", "-y")]
    instruccion = " ".join(argv).strip()

    if instruccion:
        procesar_instruccion(build_agent(), instruccion, auto_confirmar=auto_yes)
    else:
        _repl()
