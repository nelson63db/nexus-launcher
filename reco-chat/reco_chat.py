#!/usr/bin/env python3
"""
reco_chat.py — chat en terminal para administrar las tareas de reco
(codex-reminder-chirho) hablando en lenguaje natural.

Usa un modelo local servido por LM Studio a través de su API compatible con
OpenAI, y le da como herramientas todos los comandos de reco (ver
reco_backend.py). Sin dependencias externas: solo stdlib.

Variables de entorno:
    RECO_CHAT_MODEL      modelo a usar        (default: prism-ml/bonsai-27b)
    RECO_CHAT_BASE_URL   endpoint de LM Studio (default: http://localhost:1234/v1)
    RECO_CHAT_AUTO       "1" para no pedir confirmación en acciones que escriben
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

import reco_backend as rb

MODEL = os.environ.get("RECO_CHAT_MODEL", "prism-ml/bonsai-27b")
BASE_URL = os.environ.get("RECO_CHAT_BASE_URL", "http://localhost:1234/v1").rstrip("/")
AUTO_DEFAULT = os.environ.get("RECO_CHAT_AUTO", "") == "1"
LMS_BIN = os.path.expanduser("~/.lmstudio/bin/lms")

REQUEST_TIMEOUT = 900     # un 27B en CPU puede tardar
MAX_TOOL_ROUNDS = 8       # corta bucles de tool calling desbocados


class C:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    GREY = "\033[90m"


# ---------------------------------------------------------------------------
# Cliente HTTP
# ---------------------------------------------------------------------------


class LMStudioError(Exception):
    pass


def _post(path: str, payload: dict, timeout: int = REQUEST_TIMEOUT) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(cuerpo)["error"]["message"]
        except Exception:
            msg = cuerpo[:500]
        raise LMStudioError(msg) from None
    except urllib.error.URLError as e:
        raise LMStudioError(f"no pude conectar con LM Studio en {BASE_URL} ({e.reason})") from None


def _get(path: str, timeout: int = 10) -> dict:
    try:
        with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=timeout) as r:
            return json.load(r)
    except Exception as e:
        raise LMStudioError(str(e)) from None


# ---------------------------------------------------------------------------
# Arranque: servidor + modelo
# ---------------------------------------------------------------------------


def _servidor_vivo() -> bool:
    try:
        _get("/models", timeout=5)
        return True
    except LMStudioError:
        return False


def _arrancar_servidor() -> bool:
    if not os.path.isfile(LMS_BIN):
        return False
    print(f"  {C.DIM}levantando el servidor de LM Studio...{C.RESET}")
    try:
        subprocess.run([LMS_BIN, "server", "start"], capture_output=True, timeout=60)
    except Exception:
        return False
    time.sleep(2)
    return _servidor_vivo()


def _explicar_fallo_de_carga(mensaje: str) -> None:
    """bonsai-27b carga bien normalmente; si falla, lo primero a sospechar es CUDA."""
    print(f"\n  {C.RED}✖ LM Studio no pudo cargar el modelo '{MODEL}'.{C.RESET}")
    print(f"  {C.DIM}{mensaje}{C.RESET}\n")
    print(f"  {C.YELLOW}Revisá primero si CUDA está sano (esto ya rompió la carga antes):{C.RESET}")
    print(f"    {C.CYAN}python3 -c \"import ctypes; print(ctypes.CDLL('libcuda.so.1').cuInit(0))\"{C.RESET}")
    print(f"    {C.DIM}0 = OK. Si da 999, reiniciá la máquina.{C.RESET}\n")
    print(f"  {C.YELLOW}Si CUDA está sano y aun así falla, probá con otro modelo o revisá la config de carga en LM Studio.{C.RESET}\n")
    print(f"  {C.DIM}Mientras tanto podés apuntar a otro modelo:{C.RESET}")
    print(f"    {C.CYAN}RECO_CHAT_MODEL=<otro-modelo> python3 reco_chat.py{C.RESET}")
    try:
        modelos = [m["id"] for m in _get("/models").get("data", [])]
        print(f"  {C.DIM}Disponibles: {', '.join(modelos)}{C.RESET}")
    except LMStudioError:
        pass
    print()


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------


class Spinner:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, texto: str):
        self.texto = texto
        self._stop = threading.Event()
        self._hilo: threading.Thread | None = None

    def __enter__(self):
        if sys.stdout.isatty():
            self._hilo = threading.Thread(target=self._girar, daemon=True)
            self._hilo.start()
        return self

    def _girar(self):
        i, t0 = 0, time.time()
        while not self._stop.is_set():
            f = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f"\r  {C.MAGENTA}{f}{C.RESET} {C.DIM}{self.texto} ({time.time()-t0:.0f}s){C.RESET}\033[K")
            sys.stdout.flush()
            i += 1
            time.sleep(0.12)

    def __exit__(self, *_):
        self._stop.set()
        if self._hilo:
            self._hilo.join(timeout=1)
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def system_prompt() -> str:
    hoy = datetime.now()
    repos = rb.listar_repos()
    return (
        "Sos un asistente que administra las tareas y recordatorios de Nelson en 'reco' "
        "(codex-reminder-chirho), desde la terminal.\n\n"
        f"Hoy es {hoy.strftime('%Y-%m-%d')} ({hoy.strftime('%A')}).\n\n"
        "Reglas:\n"
        "- Respondé siempre en español, breve y directo. Nada de relleno.\n"
        "- Para actuar sobre las tareas usá SIEMPRE las herramientas; no inventes ids ni resultados.\n"
        "- Al crear una tarea para un repo puntual, pasá el nombre del repo en 'repo': el sistema "
        "resuelve solo si es personal o de trabajo. No inventes códigos de proyecto.\n"
        "- Si la tarea NO es de un repo puntual sino personal en general o de trabajo en general, "
        "pasá 'repo'='personal' o 'repo'='trabajo' (no es un repo real, es el catch-all correcto).\n"
        "- Si el nombre del repo no coincide con ninguno, la herramienta te devuelve sugerencias: "
        "preguntale a Nelson cuál era en vez de adivinar.\n"
        "- 'titulo' es una frase corta; el detalle largo va en 'detalles'.\n"
        "- Si te falta un dato imprescindible (ej. qué tarea), preguntá antes de llamar la herramienta.\n"
        "- Después de ejecutar algo, confirmá en una línea qué pasó (incluyendo el id de la tarea).\n\n"
        f"Repos personales: {', '.join(repos['personales'])}\n"
        f"Repos de trabajo: {', '.join(repos['trabajo'])}\n"
    )


def _pedir_confirmacion(nombre: str, args: dict) -> bool:
    render = ", ".join(f"{k}={v!r}" for k, v in args.items() if v not in (None, "", False))
    print(f"  {C.YELLOW}⚙{C.RESET} {C.BOLD}{nombre}{C.RESET}({render})")
    try:
        r = input(f"  {C.YELLOW}?{C.RESET} ¿Ejecutar? {C.DIM}[S/n]{C.RESET}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return r in ("", "s", "si", "sí", "y", "yes")


def _ejecutar(nombre: str, args: dict, auto: bool) -> dict:
    if nombre in rb.READ_ONLY or auto:
        render = ", ".join(f"{k}={v!r}" for k, v in args.items() if v not in (None, "", False))
        print(f"  {C.GREY}⚙ {nombre}({render}){C.RESET}")
    elif not _pedir_confirmacion(nombre, args):
        print(f"  {C.DIM}cancelado{C.RESET}")
        return {"error": "El usuario canceló esta acción. No la reintentes; preguntale qué prefiere."}

    resultado = rb.ejecutar_tool(nombre, args)
    if "error" in resultado:
        print(f"  {C.RED}✖ {resultado['error']}{C.RESET}")
    else:
        salida = resultado["ok"]
        texto = salida.get("salida") if isinstance(salida, dict) else None
        if texto:
            for linea in str(texto).splitlines():
                print(f"  {C.GREEN}│{C.RESET} {linea}")
        else:
            print(f"  {C.GREEN}✔{C.RESET} listo")
    return resultado


def conversar(mensajes: list, auto: bool) -> None:
    """Un turno completo: llama al modelo y resuelve todas sus tool calls."""
    for _ in range(MAX_TOOL_ROUNDS):
        with Spinner(f"{MODEL} pensando..."):
            data = _post("/chat/completions", {
                "model": MODEL,
                "messages": mensajes,
                "tools": rb.TOOLS_SPEC,
                "temperature": 0.3,
            })

        msg = data["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        contenido = (msg.get("content") or "").strip()

        # Guardamos una versión limpia (algunos modelos agregan campos extra
        # que el server rechaza si se los devolvemos tal cual).
        limpio = {"role": "assistant", "content": msg.get("content") or ""}
        if tool_calls:
            limpio["tool_calls"] = tool_calls
        mensajes.append(limpio)

        if not tool_calls:
            print(f"\n  {C.CYAN}reco{C.RESET} {contenido or '(sin respuesta)'}\n")
            return

        if contenido:
            print(f"\n  {C.CYAN}reco{C.RESET} {contenido}")

        for tc in tool_calls:
            fn = tc.get("function", {})
            nombre = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            resultado = _ejecutar(nombre, args, auto)
            mensajes.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": json.dumps(resultado, ensure_ascii=False, default=str),
            })
        print()

    print(f"  {C.YELLOW}⚠ corté el bucle tras {MAX_TOOL_ROUNDS} rondas de herramientas.{C.RESET}\n")


AYUDA = f"""
  {C.CYAN}Comandos{C.RESET}
    {C.YELLOW}/tareas{C.RESET}    lista las tareas abiertas (directo, sin pasar por el modelo)
    {C.YELLOW}/auto{C.RESET}      alterna confirmar o no las acciones que escriben
    {C.YELLOW}/reset{C.RESET}     olvida la conversación y arranca de cero
    {C.YELLOW}/modelo{C.RESET}    muestra qué modelo está en uso
    {C.YELLOW}/ayuda{C.RESET}     esto
    {C.YELLOW}/salir{C.RESET}     salir (o Ctrl+D)

  {C.DIM}Ejemplos:
    crea una tarea para intranet: proceso de facturación desde drive
    ¿qué tengo pendiente?
    marca la R0024 como hecha
    pasa la R0018 a prioridad alta y vence mañana{C.RESET}
"""


def main() -> int:
    auto = AUTO_DEFAULT

    print(f"\n{C.CYAN}{C.BOLD}  ╔═══════════════════════════════════════════╗{C.RESET}")
    print(f"{C.CYAN}{C.BOLD}  ║   RECO · chat de tareas                   ║{C.RESET}")
    print(f"{C.CYAN}{C.BOLD}  ╚═══════════════════════════════════════════╝{C.RESET}")
    print(f"  {C.DIM}modelo:{C.RESET} {MODEL}   {C.DIM}via{C.RESET} {BASE_URL}")

    if not _servidor_vivo() and not _arrancar_servidor():
        print(f"\n  {C.RED}✖ LM Studio no responde en {BASE_URL}.{C.RESET}")
        print(f"  Arrancalo con {C.CYAN}lms server start{C.RESET} (o abriendo la app) y volvé a intentar.\n")
        return 1

    try:
        abiertas = rb.listar_tareas()["salida"]
        n = len([l for l in abiertas.splitlines() if l.strip().startswith("R")])
        print(f"  {C.DIM}tareas abiertas:{C.RESET} {n}")
    except rb.RecoError as e:
        print(f"  {C.YELLOW}⚠ no pude leer las tareas: {e}{C.RESET}")

    print(f"  {C.DIM}/ayuda para los comandos · Ctrl+D para salir{C.RESET}\n")

    mensajes = [{"role": "system", "content": system_prompt()}]

    while True:
        try:
            entrada = input(f"  {C.MAGENTA}tú{C.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not entrada:
            continue

        if entrada.startswith("/"):
            cmd = entrada.lower().split()[0]
            if cmd in ("/salir", "/exit", "/quit"):
                break
            if cmd == "/ayuda":
                print(AYUDA)
            elif cmd == "/auto":
                auto = not auto
                estado = "SIN confirmar (cuidado)" if auto else "confirmando cada acción"
                print(f"  {C.DIM}modo: {estado}{C.RESET}\n")
            elif cmd == "/reset":
                mensajes = [{"role": "system", "content": system_prompt()}]
                print(f"  {C.DIM}conversación reiniciada{C.RESET}\n")
            elif cmd == "/modelo":
                print(f"  {MODEL} {C.DIM}via {BASE_URL}{C.RESET}\n")
            elif cmd == "/tareas":
                try:
                    print()
                    for linea in rb.listar_tareas()["salida"].splitlines():
                        print(f"  {linea}")
                    print()
                except rb.RecoError as e:
                    print(f"  {C.RED}✖ {e}{C.RESET}\n")
            else:
                print(f"  {C.DIM}comando desconocido; probá /ayuda{C.RESET}\n")
            continue

        mensajes.append({"role": "user", "content": entrada})

        try:
            conversar(mensajes, auto)
        except LMStudioError as e:
            if "Failed to load model" in str(e) or "Error loading model" in str(e):
                _explicar_fallo_de_carga(str(e))
                return 1
            print(f"\n  {C.RED}✖ {e}{C.RESET}\n")
            mensajes.pop()
        except KeyboardInterrupt:
            print(f"\n  {C.DIM}interrumpido{C.RESET}\n")
            mensajes.pop()

    print(f"\n  {C.CYAN}Listo. Hasta luego.{C.RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
