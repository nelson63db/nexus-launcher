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
