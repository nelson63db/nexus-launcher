# reco-chat

Chat en lenguaje natural sobre las tareas de reco (`reco_chat.py`, `reco_backend.py`).

## reco corre en MODO REMOTO por defecto

La fuente de verdad de las tareas es el servidor (app `reco` de SWDprod, `https://smart-web-dev.com`).
Cuando el usuario pida algo de reco (crear, listar, completar, mover, subtareas…), usá el CLI de siempre:

```bash
/Users/nelson/.local/bin/codex-reminder-chirho <comando>
```

El CLI ya reenvía al servidor solo (credenciales en `~/.codex/reminders-chirho/remote.env`, `RECO_URL` + `RECO_TOKEN`)
y refresca `~/.codex/reminders-chirho/tasks.json` como **espejo de solo lectura** después de cada comando.

- **NUNCA edites `tasks.json` a mano** ni escribas ahí: se pisa en el próximo comando y el servidor no se entera.
- `--local` / `RECO_LOCAL=1` fuerza el store local: solo para emergencias, y divergen. No lo uses sin que el usuario lo pida.
- Si el servidor no responde: lecturas (`list`, `projects`…) caen al espejo con un aviso; las escrituras fallan sin escribir nada. Avisale al usuario.
- Si aparece "hay RECO_URL pero falta RECO_TOKEN": falta pegar el token en `remote.env`.
- Comandos que siguen siendo locales (leen la laptop): `sync-sessions`, `pulse`, `stats`, `serve`, `check`, `render`, `open`, `habitica-sync`. Habitica ya lo sincroniza el servidor.
- Para título de una tarea no hay comando del CLI: usá la tool `rename_task` por HTTP (`POST {RECO_URL}/api/reco/tools/rename_task/`) o `editar_tarea` de `reco_backend.py`.
- Runbook del servidor: `~/PycharmProjects/SWDprod/docs-ia/infra/reco-vps.md`.

## Bases de datos de Nelson (Tabulia) — tools `db_*`

Además de las tareas, desde acá se administran las bases de datos de Tabulia (app `tabulia` de SWDprod): consultar,
guardar, cambiar, archivar, pedir cambios de estructura al arquitecto y crear **avisos** («avísame cuando…»,
«cada lunes mándame…», que llegan por Telegram vía Rocko). Son tools del MISMO servidor de reco, por HTTP:

```bash
set -a; . ~/.codex/reminders-chirho/remote.env; set +a      # RECO_URL + RECO_TOKEN
# guía (lenguaje de consultas, avisos, reglas): la sección "## Bases de datos" del final
curl -s "$RECO_URL/api/reco/guide/" -H "Authorization: Bearer $RECO_TOKEN" | sed -n '/^## Bases de datos/,$p'
# llamar una tool (los argumentos estructurados van como JSON en texto: query_json, records_json, set_json…)
curl -s -X POST "$RECO_URL/api/reco/tools/db_list/" -H "Authorization: Bearer $RECO_TOKEN" \
     -H 'Content-Type: application/json' -d '{}'
curl -s -X POST "$RECO_URL/api/reco/tools/db_query/" -H "Authorization: Bearer $RECO_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"base": "gastos", "query_json": "{\"from\": \"gastos\", \"group\": [\"categoria\"], \"agg\": {\"t\": [\"sum\", \"monto\"]}}"}'
```

- Catálogo completo con esquemas: `GET $RECO_URL/api/reco/tools/?format=mcp` (las `db_*`; `readOnlyHint` = solo lee).
- Reglas: mirá `db_describe` antes de guardar o consultar (no inventes tablas ni columnas); archivar varios registros pide
  `confirm=true` después de preguntarle a Nelson; los cambios de estructura van por `db_structure` (asíncrono, el
  arquitecto avisa por Telegram) y se aplican SOLO cuando Nelson lo pide (`db_apply_plan`). Nada se borra: se archiva.
- El chat de terminal (`reco_chat.py`) ya carga solas estas tools al arrancar (`cargar_tools_remotas`).
- Alternativa: el servidor MCP de reco (`$RECO_URL/api/reco/mcp/`, mismo token) expone las mismas tools.

## Rocko con Claude (`rocko_claude_worker.py`)

El panel «Chat» del launcher arranca `rocko_claude_worker.py --background`: mientras el proyecto reco está
abierto, el bot de Telegram contesta con `claude -p` (Sonnet, esfuerzo bajo, cuenta personal
`~/.claude-personal`) en vez de Gemini. Ver README § «Rocko con Claude».

- **Nunca** apuntarlo a `~/.claude` (instalación de trabajo) ni pasarle una `ANTHROPIC_API_KEY`.
- El mismo worker atiende la cola de **EasyDoc** (`/api/v1/easydoc/claude/laptop/…`, SWDprod `api_apps/easydoc/claude_queue.py`):
  Sonnet con esfuerzo alto, sin tools, texto por trozos. Un hilo por trabajo; la pregunta de Rocko sigue siendo el latido.
  Si se cambia el protocolo acá, cambiarlo allá (y en `api_apps/easydoc/test_claude.py`). Para parar el worker usa
  `--stop`; **nunca** `pkill`/`kill` por patrón.
- Diagnóstico: `python3 rocko_claude_worker.py --status` y `~/.cache/rocko-claude/worker.log`.
- El protocolo (claim/done/fail, lease, `X-Rocko-Job`) lo define el servidor: SWDprod `reco/rocko_queue.py`.
  Si se cambia acá, cambiarlo allá (y sus tests en `reco/tests_rocko.py`).
