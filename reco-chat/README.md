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
