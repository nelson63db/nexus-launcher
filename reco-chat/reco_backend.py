"""
reco_backend.py — capa de herramientas sobre codex-reminder-chirho ("reco").

Expone cada comando de reco como una función Python normal, más los esquemas
en formato OpenAI (`TOOLS_SPEC`) para pasárselos a un modelo con tool calling.

Resolución de proyecto
-----------------------
Dado el nombre de un repo (exacto, parcial o mal escrito), se busca la carpeta
real en:

  - ~/PycharmProjects/*  y  ~/napps/*   -> personal -> project = "personal/<Carpeta>"
  - ~/dev-chirho/*                      -> trabajo  -> project = codigo (SR, INT, ...)

Si no hay match, se devuelve un error con sugerencias en vez de crear la tarea
en un proyecto inventado.
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config / rutas
# ---------------------------------------------------------------------------

HOME = Path.home()
RECO_BIN = str(HOME / ".local" / "bin" / "codex-reminder-chirho")
TASKS_PATH = HOME / ".codex" / "reminders-chirho" / "tasks.json"

PERSONAL_ROOTS = [HOME / "PycharmProjects", HOME / "napps"]
WORK_ROOT = HOME / "dev-chirho"

# Tabla de deteccion de proyecto copiada de ~/.claude/CLAUDE.md (Equipo DIE).
# Ese archivo NO se modifica desde esta cuenta; si su tabla cambia, actualizar
# esta copia a mano.
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

ESTADOS = ["pending", "in_progress", "review", "done", "blocked", "cancelled"]
PRIORIDADES = ["high", "medium", "low"]

# Alias de scope genérico: cuando piden una tarea "personal" o "de trabajo" sin
# nombrar un repo puntual, no hay que buscarlo como si fuera una carpeta.
# "personal/home" y "general" ya son las convenciones usadas a mano en reco
# para ese caso (ver tasks.json).
SCOPE_ALIASES: dict[str, dict] = {
    "personal": {"scope": "personal", "project": "personal/home"},
    "personales": {"scope": "personal", "project": "personal/home"},
    "personal/home": {"scope": "personal", "project": "personal/home"},
    "trabajo": {"scope": "trabajo", "project": "general"},
    "general": {"scope": "trabajo", "project": "general"},
}


class RecoError(Exception):
    """Error al ejecutar un comando de reco o al resolver un repo."""


# ---------------------------------------------------------------------------
# Resolución de repo -> scope / proyecto
# ---------------------------------------------------------------------------


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _list_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]


def _candidates() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for root in PERSONAL_ROOTS:
        out += [(p, "personal") for p in _list_dirs(root)]
    out += [(p, "trabajo") for p in _list_dirs(WORK_ROOT)]
    return out


def _work_project_code(folder_name: str) -> str:
    lowered = folder_name.lower()
    for keywords, code in WORK_PROJECT_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return code
    return "OTH"


def _resolution(path: Path, scope: str) -> dict:
    project = f"personal/{path.name}" if scope == "personal" else _work_project_code(path.name)
    return {"scope": scope, "project": project, "path": str(path), "repo": path.name}


def resolve_project(repo: str) -> dict:
    """Resuelve el nombre de un repo a su scope (personal/trabajo) y su --project."""
    query = _normalize(repo)
    if not query:
        raise RecoError("No me diste ningún repo.")

    alias = SCOPE_ALIASES.get(repo.strip().lower())
    if alias:
        return {**alias, "path": None, "repo": None}

    cands = _candidates()

    for path, scope in cands:
        if path.name.lower() == repo.lower():
            return _resolution(path, scope)

    subs = [(p, s) for p, s in cands if query in _normalize(p.name) or _normalize(p.name) in query]
    if len(subs) == 1:
        return _resolution(*subs[0])
    if len(subs) > 1:
        nombres = ", ".join(sorted({p.name for p, _ in subs}))
        raise RecoError(f"'{repo}' es ambiguo, coincide con varios repos: {nombres}. Preguntá cuál es.")

    sugerencias = difflib.get_close_matches(repo, [p.name for p, _ in cands], n=5, cutoff=0.5)
    extra = f" ¿Quisiste decir: {', '.join(sugerencias)}?" if sugerencias else ""
    raise RecoError(f"No existe ningún repo que coincida con '{repo}'.{extra}")


def listar_repos() -> dict:
    """Devuelve los repos conocidos, separados en personales y de trabajo."""
    personales, trabajo = [], []
    for path, scope in _candidates():
        (personales if scope == "personal" else trabajo).append(path.name)
    return {"personales": sorted(personales), "trabajo": sorted(trabajo)}


# ---------------------------------------------------------------------------
# Ejecución del CLI de reco
# ---------------------------------------------------------------------------


def _run(args: list[str], timeout: int = 120) -> str:
    try:
        result = subprocess.run([RECO_BIN, *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RecoError(f"No encuentro el ejecutable de reco en {RECO_BIN}")
    except subprocess.TimeoutExpired:
        raise RecoError(f"El comando 'reco {' '.join(args)}' se pasó de {timeout}s.")
    if result.returncode != 0:
        raise RecoError((result.stderr or result.stdout).strip() or f"reco {' '.join(args)} falló")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Tools — una por comando de reco
# ---------------------------------------------------------------------------


def crear_tarea(repo=None, titulo=None, detalles="", vencimiento=None, prioridad="medium"):
    if not titulo:
        raise RecoError("Falta el título de la tarea.")
    if not repo:
        raise RecoError(
            "Falta 'repo': decime el nombre del repo, o si es personal/trabajo en general "
            "sin repo puntual pasá repo='personal' o repo='trabajo'. No asumas ninguno."
        )
    args = ["add", titulo]
    resuelto = resolve_project(repo)
    args += ["--project", resuelto["project"]]
    if prioridad:
        if prioridad not in PRIORIDADES:
            raise RecoError(f"Prioridad inválida: {prioridad}. Usá {', '.join(PRIORIDADES)}.")
        args += ["--priority", prioridad]
    if detalles:
        args += ["--details", detalles]
    if vencimiento:
        args += ["--due", vencimiento]
    return {"salida": _run(args), "proyecto": resuelto}


def listar_tareas(todas=False):
    return {"salida": _run(["list", "--all"] if todas else ["list"])}


def marcar_hecha(id=None):
    if not id:
        raise RecoError("Falta el id de la tarea (ej. R0012).")
    return {"salida": _run(["done", id])}


def cambiar_estado(id=None, estado=None):
    if not id or not estado:
        raise RecoError("Necesito el id y el estado nuevo.")
    if estado not in ESTADOS:
        raise RecoError(f"Estado inválido: {estado}. Usá {', '.join(ESTADOS)}.")
    return {"salida": _run(["status", id, estado])}


def cambiar_prioridad(id=None, prioridad=None):
    if not id or not prioridad:
        raise RecoError("Necesito el id y la prioridad nueva.")
    if prioridad not in PRIORIDADES:
        raise RecoError(f"Prioridad inválida: {prioridad}. Usá {', '.join(PRIORIDADES)}.")
    return {"salida": _run(["priority", id, prioridad])}


def cambiar_vencimiento(id=None, vencimiento=None):
    if not id or not vencimiento:
        raise RecoError("Necesito el id y la fecha nueva.")
    return {"salida": _run(["due", id, vencimiento])}


def enfocar_tarea(id=None):
    if not id:
        raise RecoError("Falta el id de la tarea a enfocar.")
    return {"salida": _run(["focus", id])}


def quitar_foco():
    return {"salida": _run(["focus", "--clear"])}


def vincular_sesion(id=None, sesion=None):
    if not id:
        raise RecoError("Falta el id de la tarea.")
    args = ["link", id]
    if sesion:
        args += ["--session", sesion]
    return {"salida": _run(args)}


def ver_vencidas():
    return {"salida": _run(["due"])}


def ver_estadisticas():
    return {"salida": _run(["stats"], timeout=300)}


def sincronizar_habitica(cuenta="all"):
    if cuenta not in ("trabajo", "personal", "all"):
        raise RecoError("cuenta debe ser trabajo, personal o all.")
    return {"salida": _run(["habitica-sync", "--account", cuenta], timeout=300)}


def sincronizar_sesiones():
    return {"salida": _run(["sync-sessions", "--quiet"], timeout=300) or "sesiones sincronizadas"}


def revisar_pendientes():
    return {"salida": _run(["check"], timeout=300)}


def ver_pulso():
    return {"salida": _run(["pulse"], timeout=300)}


def renderizar_dashboard():
    return {"salida": _run(["render"], timeout=300) or "dashboard regenerado"}


def abrir_dashboard():
    return {"salida": _run(["open"], timeout=60) or "dashboard abierto"}


def editar_tarea(id=None, titulo=None, detalles=None, repo=None):
    """Edita título/detalles/proyecto. La CLI de reco no expone esto, así que
    se escribe directo en tasks.json. No sincroniza con Habitica."""
    if not id:
        raise RecoError("Falta el id de la tarea.")
    if titulo is None and detalles is None and repo is None:
        raise RecoError("Nada que editar: pasá titulo, detalles y/o repo.")
    if not TASKS_PATH.is_file():
        raise RecoError(f"No existe el store de tareas: {TASKS_PATH}")

    data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    tarea = next((t for t in data.get("tasks_chirho", []) if t.get("id_chirho") == id), None)
    if tarea is None:
        raise RecoError(f"No existe la tarea {id}.")

    resuelto = None
    if titulo is not None:
        tarea["title_chirho"] = titulo
    if detalles is not None:
        tarea["details_chirho"] = detalles
    if repo is not None:
        resuelto = resolve_project(repo)
        tarea["project_chirho"] = resuelto["project"]

    tarea["updated_at_chirho"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    TASKS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"tarea": tarea, "proyecto": resuelto}


# ---------------------------------------------------------------------------
# Registro de tools
# ---------------------------------------------------------------------------

# Tools que solo leen: se ejecutan sin pedir confirmación.
READ_ONLY = {
    "listar_tareas",
    "ver_vencidas",
    "ver_estadisticas",
    "listar_repos",
    "ver_pulso",
}

TOOL_FUNCS = {
    "crear_tarea": crear_tarea,
    "listar_tareas": listar_tareas,
    "marcar_hecha": marcar_hecha,
    "cambiar_estado": cambiar_estado,
    "cambiar_prioridad": cambiar_prioridad,
    "cambiar_vencimiento": cambiar_vencimiento,
    "enfocar_tarea": enfocar_tarea,
    "quitar_foco": quitar_foco,
    "vincular_sesion": vincular_sesion,
    "ver_vencidas": ver_vencidas,
    "ver_estadisticas": ver_estadisticas,
    "sincronizar_habitica": sincronizar_habitica,
    "sincronizar_sesiones": sincronizar_sesiones,
    "revisar_pendientes": revisar_pendientes,
    "ver_pulso": ver_pulso,
    "renderizar_dashboard": renderizar_dashboard,
    "abrir_dashboard": abrir_dashboard,
    "editar_tarea": editar_tarea,
    "listar_repos": lambda: listar_repos(),
}


def _tool(name: str, description: str, properties: dict | None = None, required: list | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        },
    }


TOOLS_SPEC = [
    _tool(
        "crear_tarea",
        "Crea una tarea/recordatorio nuevo. Si la tarea es para un repositorio o proyecto puntual, "
        "pasá su nombre en 'repo' y el sistema resuelve solo si es personal o de trabajo. Si NO es de "
        "un repo puntual sino personal/trabajo en general, pasá 'repo'='personal' o 'repo'='trabajo'.",
        {
            "repo": {"type": "string", "description": "nombre del repo/proyecto (ej. 'intranet', 'Nostr_Music_Project'), o 'personal'/'trabajo' si no es de un repo puntual"},
            "titulo": {"type": "string", "description": "título breve y claro de la tarea"},
            "detalles": {"type": "string", "description": "contexto adicional; acá va el detalle largo"},
            "vencimiento": {"type": "string", "description": "fecha límite: YYYY-MM-DD, 'hoy' o 'manana'"},
            "prioridad": {"type": "string", "enum": PRIORIDADES},
        },
        ["titulo", "repo"],
    ),
    _tool(
        "listar_tareas",
        "Lista las tareas. Por defecto solo las abiertas; con todas=true incluye hechas y canceladas.",
        {"todas": {"type": "boolean"}},
    ),
    _tool("marcar_hecha", "Marca una tarea como hecha (done).",
          {"id": {"type": "string", "description": "id de la tarea, ej. R0012"}}, ["id"]),
    _tool("cambiar_estado", "Cambia el estado de una tarea.",
          {"id": {"type": "string"}, "estado": {"type": "string", "enum": ESTADOS}}, ["id", "estado"]),
    _tool("cambiar_prioridad", "Cambia la prioridad de una tarea.",
          {"id": {"type": "string"}, "prioridad": {"type": "string", "enum": PRIORIDADES}}, ["id", "prioridad"]),
    _tool("cambiar_vencimiento", "Cambia la fecha de vencimiento de una tarea.",
          {"id": {"type": "string"}, "vencimiento": {"type": "string", "description": "YYYY-MM-DD, 'hoy' o 'manana'"}},
          ["id", "vencimiento"]),
    _tool("editar_tarea", "Edita el título, los detalles o el repo/proyecto de una tarea ya creada.",
          {"id": {"type": "string"}, "titulo": {"type": "string"}, "detalles": {"type": "string"},
           "repo": {"type": "string"}}, ["id"]),
    _tool("enfocar_tarea", "Marca una tarea como la tarea activa en la que se está trabajando ahora.",
          {"id": {"type": "string"}}, ["id"]),
    _tool("quitar_foco", "Quita el foco de la tarea activa, sin marcarla como hecha."),
    _tool("vincular_sesion", "Vincula una sesión de Codex/Claude a una tarea.",
          {"id": {"type": "string"}, "sesion": {"type": "string", "description": "id de sesión; si se omite usa la más reciente"}},
          ["id"]),
    _tool("ver_vencidas", "Lista las tareas vencidas o que vencen hoy."),
    _tool("ver_estadisticas", "Muestra estadísticas: sesiones, tiempo activo y tokens por modelo."),
    _tool("listar_repos", "Lista los repos disponibles, separados en personales y de trabajo. "
                          "Usalo si no estás seguro del nombre exacto de un repo."),
    _tool("sincronizar_habitica", "Sincroniza las tareas con Habitica.",
          {"cuenta": {"type": "string", "enum": ["trabajo", "personal", "all"]}}),
    _tool("sincronizar_sesiones", "Sincroniza el worklog con las sesiones de Codex/Claude."),
    _tool("revisar_pendientes", "Revisa pendientes y dispara las notificaciones de tareas vencidas."),
    _tool("ver_pulso", "Muestra el pulso/resumen rápido del estado de las tareas."),
    _tool("renderizar_dashboard", "Regenera el HTML del dashboard de reco."),
    _tool("abrir_dashboard", "Abre el dashboard de reco en el navegador."),
]


def ejecutar_tool(nombre: str, argumentos: dict) -> dict:
    """Ejecuta una tool por nombre. Devuelve {'ok': ...} o {'error': ...}."""
    fn = TOOL_FUNCS.get(nombre)
    if fn is None:
        return {"error": f"No existe la herramienta '{nombre}'."}
    try:
        return {"ok": fn(**(argumentos or {}))}
    except RecoError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"Argumentos inválidos para {nombre}: {e}"}
    except Exception as e:  # noqa: BLE001 - el modelo debe poder ver cualquier fallo
        return {"error": f"{type(e).__name__}: {e}"}
