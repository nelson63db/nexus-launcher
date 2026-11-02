# reco-chat

Chat en terminal para administrar las tareas de **reco** (`codex-reminder-chirho`)
hablando en lenguaje natural, usando un modelo local servido por **LM Studio**.

Se abre desde el launcher NEXUS: `dev` → `reco`.

## Cómo funciona

```
vos escribís  ──►  modelo local (LM Studio)  ──►  tool call  ──►  codex-reminder-chirho
                          ▲                                              │
                          └──────────── resultado ◄──────────────────────┘
```

- `reco_backend.py` — cada comando de reco expuesto como función Python, más los
  esquemas de tools en formato OpenAI (`TOOLS_SPEC`, 19 herramientas).
- `reco_chat.py` — el chat: cliente HTTP, bucle de tool calling y confirmaciones.

Sin dependencias externas, solo stdlib. No hace falta venv.

## Uso

```sh
python3 reco_chat.py
```

```
  tú crea una tarea para intranet: proceso de facturación desde drive
  ⚙ crear_tarea(repo='intranet', titulo='...', prioridad='medium')
  ? ¿Ejecutar? [S/n]:
  │ Creado R0034: proceso de facturación desde drive
  reco Listo, creé R0034 en el proyecto INT.
```

Las herramientas que **solo leen** (listar, ver vencidas, estadísticas) se ejecutan
solas. Las que **escriben** piden confirmación; `/auto` desactiva ese pedido.

### Bases de datos (Tabulia)

Al arrancar, el chat le pide al servidor de reco su catálogo y suma las tools `db_*` (las bases de datos de
Tabulia: consultar, guardar, avisos, cambios de estructura). No hay copia local: si el servidor agrega o cambia
una, aparece sola. Las que solo leen se ejecutan sin confirmar (vienen marcadas en el catálogo MCP). La guía del
lenguaje de consultas también llega del servidor y entra en el prompt de sistema.

### Rocko con Claude (`rocko_claude_worker.py`)

Mientras el proyecto `reco` está abierto en el launcher, **Rocko (el bot de Telegram) contesta con la
suscripción de Claude de esta máquina** en lugar de con Gemini. El panel «Chat» lo arranca solo
(`--background`) y el proceso se cierra cuando se cierra ese panel.

```
Telegram → VPS (cola de Rocko) ◄── este proceso pregunta cada 2 s
                               ──► el mensaje + instrucciones de Rocko
          claude -p --model sonnet --effort low  (tools de reco por MCP)
                               ──► respuesta
```

- Usa la cuenta **personal** (`CLAUDE_CONFIG_DIR=~/.claude-personal`; se niega a usar `~/.claude`) y quita
  `ANTHROPIC_API_KEY` del entorno: siempre la suscripción, nunca una API de pago.
- Corre en `~/.cache/rocko-claude/` (carpeta vacía), sin hooks ni ajustes del usuario (`--setting-sources ''`),
  sin herramientas de archivos ni terminal (`--tools ''`) y sin guardar la sesión: solo las tools de reco.
- Si esta máquina se apaga, se duerme o se cierra el panel, en ~15 s el servidor contesta con Gemini
  (`gemini-flash-latest` → `gemini-flash-lite-latest`) y reintenta hasta que alguno pueda. Los audios van
  siempre a Gemini (Claude no oye).
- Si se acaba la cuota de Claude, el servidor no le manda nada a esta máquina por 30 min.

```sh
python3 rocko_claude_worker.py --status          # ¿corre? ¿qué ve el servidor?
python3 rocko_claude_worker.py --stop
python3 rocko_claude_worker.py --background --model opus --effort medium   # otro modelo
tail -f ~/.cache/rocko-claude/worker.log
```

**También atiende a EasyDoc** (desde la versión 2 del worker): mientras esté corriendo, los documentos que
generan en `smart-web-dev.com/easydoc` los superusers —y los usuarios a los que les diste la clave de permiso
`use_claude_sonnet` en el admin de Django— los escribe Claude Sonnet con esfuerzo **alto** en esta máquina,
y el navegador va viendo el texto mientras se escribe. Sin esta máquina, EasyDoc usa Gemini (flash → flash-lite).
Cada documento corre en su propio hilo (hasta 2 a la vez) con el modelo/esfuerzo que pide el servidor, sin tools
ni MCP; Rocko sigue con el suyo (`low`). Si el usuario pulsa Detener o cierra la pestaña, el proceso se mata.

```sh
python3 rocko_claude_worker.py --background --no-easydoc     # solo Rocko
python3 rocko_claude_worker.py --background --easydoc-slots 1 # un documento a la vez
ROCKO_CLAUDE_STATE_DIR=/tmp/x python3 rocko_claude_worker.py  # 2ª instancia de prueba (otro lock/pid/log)
```

⚠️ Un worker en marcha no recarga el archivo: tras actualizarlo, `--stop` y reabrir el panel «Chat» de reco.
Nunca pares el worker con `pkill`/`kill` por patrón (puede llevarse otros procesos): `--stop` o el PID de `--status`.

Variables: `ROCKO_CLAUDE_MODEL` (default `sonnet`), `ROCKO_CLAUDE_EFFORT` (default `low`). En Telegram,
`/motor` dice quién está contestando. Del lado del servidor: SWDprod `reco/rocko_queue.py` y
`docs-ia/infra/reco-vps.md` § «Quién contesta» (hace falta el worker `swd-reco-rocko` en la VPS).

### Comandos del chat

| Comando | Qué hace |
|---|---|
| `/tareas` | lista las tareas abiertas sin pasar por el modelo |
| `/auto` | alterna confirmar / no confirmar las acciones que escriben |
| `/reset` | olvida la conversación |
| `/modelo` | muestra el modelo en uso |
| `/ayuda` | ayuda |
| `/salir` | salir (o Ctrl+D) |

### Variables de entorno

| Variable | Default |
|---|---|
| `RECO_CHAT_MODEL` | `prism-ml/bonsai-27b` |
| `RECO_CHAT_BASE_URL` | `http://localhost:1234/v1` |
| `RECO_CHAT_AUTO` | `1` para no confirmar nada |

## Resolución de repos

Cuando pedís una tarea "para el repo X", el nombre se resuelve contra las carpetas
reales del disco, y de ahí sale si la tarea es personal o de trabajo:

| Carpeta | Scope | `--project` |
|---|---|---|
| `~/PycharmProjects/*`, `~/napps/*` | personal | `personal/<Carpeta>` |
| `~/dev-chirho/*` | trabajo | código de la tabla (`SR`, `INT`, `TM`, …) o `OTH` |

Si el nombre no coincide con nada, la herramienta devuelve sugerencias y el modelo
tiene instrucción de preguntar en vez de inventar un proyecto.

Si la tarea **no** es de un repo puntual sino personal o de trabajo en general, se
pasa `repo='personal'` o `repo='trabajo'`, que resuelven directo a los catch-all
`personal/home` / `general` (sin buscar carpeta) en vez de intentar matchear esas
palabras como si fueran el nombre de un repo.

> La tabla de códigos de trabajo es una copia de la de `~/.claude/CLAUDE.md`
> (ese archivo no se edita desde la cuenta personal). Si allá cambia, hay que
> actualizar `WORK_PROJECT_KEYWORDS` en `reco_backend.py` a mano.

## Estado actual (2026-08-14): bonsai-27b carga y funciona

`prism-ml/bonsai-27b` **carga bien** en LM Studio estándar (Q1_0, tipo de tensor 41
incluido) desde que se reinició la máquina. La sospecha anterior — que hacía falta
compilar el fork de PrismML porque llama.cpp upstream no soportaba ese cuantizado —
**era incorrecta**. El bloqueo real era el problema de CUDA descrito abajo: con
`cuInit()` fallando, LM Studio no podía completar la carga y el error que mostraba
(`gguf_init_from_file_impl: failed to read tensor info`) apuntaba al lugar
equivocado. No hizo falta ningún fork ni compilar nada.

**Es un modelo de razonamiento** (`reasoning_content` separado en la respuesta de
la API, con sus propios `reasoning_tokens`): antes de responder genera una cadena
de pensamiento completa, incluso después de ejecutar tool calls. En 1-bit con GPU
de 8 GB corre a ~12 tok/s, así que una respuesta puede tardar uno o varios minutos
reales sin estar trabada — el spinner de `reco_chat.py` muestra los segundos
transcurridos para diferenciar "lento" de "colgado". Si el ritmo no es aceptable
en la práctica, la alternativa es un modelo más chico sin ese overhead de
razonamiento (ver recomendaciones en `ESTADO.md`).

## CUDA roto en el sistema (arreglado con reboot)

`cuInit()` devolvía **999 (`CUDA_ERROR_UNKNOWN`)** desde cualquier proceso, aunque
`nvidia-smi` funcionara y driver y módulo coincidieran (565.57.01), `nvidia_uvm`
estuviera cargado y los `/dev/nvidia*` fueran 666. Un reinicio lo arregló. Para
verificar el estado actual:

```sh
python3 -c "import ctypes; print(ctypes.CDLL('libcuda.so.1').cuInit(0))"   # 0 = OK
```

La GPU es una RTX 2070 Mobile de 8 GB; con bonsai cargado (4.73 GB) usa ~6 GB.
