# Estado y pendientes — reco-chat

> Escrito el **2026-08-13**, antes de reiniciar la máquina.
> Retomar la sesión de Claude con:
> ```sh
> claudep --resume 5628a003-b346-407e-a77b-90f2f5659571
> ```

---

## ✅ CORRECCIÓN — 2026-08-14, después del reboot

**El problema #2 de la tabla de abajo (bonsai necesita el fork de PrismML) era
diagnóstico incorrecto.** Después de reiniciar, `bonsai-27b` **carga y funciona**
en LM Studio estándar, sin tocar nada del fork. El problema real siempre fue el
#1 (CUDA roto) — el reboot lo arregló y eso solo ya destrababa la carga; el error
`gguf_init_from_file_impl: failed to read tensor info` apuntaba al lugar
equivocado. No hizo falta clonar ni compilar `prismml-llama.cpp` (ver
`~/napps/prismml-llama.cpp`, se puede borrar si no se le va a dar otro uso).

Hallazgo nuevo: **bonsai-27b es un modelo de razonamiento** — la API devuelve
`reasoning_content`/`reasoning_tokens` separados del `content` final, y genera
una cadena de pensamiento completa en cada turno (incluso después de tool calls)
antes de responder. A ~12 tok/s en 1-bit con GPU de 8 GB, una respuesta puede
tardar minutos reales sin estar trabada. Si eso resulta muy lento en la práctica
del día a día, la salida es cambiar a uno de los modelos más chicos recomendados
en la sección "Plan de modelos" más abajo — no por incompatibilidad, sino por
velocidad.

El resto de este documento (Problema 2, Opciones A/B/C, plan de compilar el fork)
queda **como referencia histórica de un diagnóstico descartado** — no hace falta
ejecutar nada de eso.

---

## TL;DR (histórico — ver corrección arriba)

El código está **terminado y probado**. No hay nada que arreglar en Python.

Lo que falta es del entorno, y son **dos problemas independientes**:

| # | Problema | Se arregla con |
|---|---|---|
| 1 | CUDA devuelve error 999 → LM Studio corre todo en CPU | **reiniciar** (gratis, probable) — ✅ confirmado, esto era todo |
| 2 | ~~`bonsai-27b` necesita kernels que solo están en el fork de PrismML~~ | ❌ descartado, ver corrección arriba |

El #1 no depende del #2. Reiniciar sirve igual, aunque nunca uses bonsai.

---

## Lo que ya funciona (verificado corriendo)

- `~/napps/reco-chat/reco_backend.py` — los 19 comandos de reco como funciones
  Python + sus esquemas de tools en formato OpenAI.
- `~/napps/reco-chat/reco_chat.py` — el chat: cliente HTTP, bucle de tool calling,
  confirmación antes de cada acción que escribe.
- Proyecto **`reco`** en el launcher (`dev` → `reco`): 3 paneles tmux, el grande
  arranca el chat, los otros dos solo hacen `cd` (libres para Claude).
- El proyecto de prueba `needle` fue quitado del launcher.

Probado de punta a punta contra el reco real: el modelo emitió un `tool_call`
bien formado, el código lo parseó, ejecutó la función y recibió las tareas reales.

```
finish_reason: tool_calls
tool_calls   : [{"function": {"name": "listar_tareas", "arguments": "{\"todas\":false}"}}]
--> ok? True | R0027 [pending] [medium] due:2026-08-10 general - Leer y revisar...
```

---

## Problema 1 — CUDA roto (reiniciar)

`cuInit()` devuelve **999 = `CUDA_ERROR_UNKNOWN`** desde cualquier proceso.

Lo raro es que todo lo demás está bien:

| Chequeo | Resultado |
|---|---|
| `nvidia-smi` | funciona, RTX 2070 Mobile, 7.6 GB libres |
| driver userspace vs módulo kernel | coinciden, ambos 565.57.01 |
| `nvidia_uvm` cargado | sí |
| `/dev/nvidia*` | existen, permisos 666 |
| uptime | 3 semanas y media |
| `cuInit(0)` | **999** ← el problema |

Consecuencia: LM Studio loguea `No CUDA devices found` y corre **todo en CPU**.
Por eso llama-3-8B tardaba minutos por respuesta teniendo una GPU de 8 GB al lado.

El error 999 con todo lo demás sano y uptime largo casi siempre se arregla
reiniciando.

### Verificar después de reiniciar

```sh
python3 -c "import ctypes; print(ctypes.CDLL('libcuda.so.1').cuInit(0))"
```

- `0` → CUDA arreglado ✅
- `999` → sigue roto; hay que mirar más a fondo (driver, Optimus/PRIME, o
  reinstalar el paquete del driver)

Y para confirmar que LM Studio ahora sí ve la GPU:

```sh
lms server start
lms load meta-llama-3-8b-instruct
# debería cargar en segundos y responder rápido, no en minutos
```

---

## Problema 2 — bonsai-27b no corre en LM Studio

**Actualizar LM Studio NO lo arregla.** (Ya se probó: `lms runtime update --all`
dice "up-to-date" y `lms load` sigue fallando.)

### Qué pasa exactamente

El `.gguf` **está completo** — verificado byte a byte: parsea el header, los 39
pares de metadata y los 851 tensores, y el último tensor termina justo donde
termina el archivo. No es una descarga corrupta.

El problema es el formato: **498 de sus 851 tensores usan tipo 41**, o sea
`Q1_0_g128`, la cuantización binaria de PrismML. Ese formato necesita los
**kernels hybrid-attention propios de PrismML**, que no están en llama.cpp
upstream. LM Studio usa llama.cpp estándar → no lo puede leer, y aborta con:

```
gguf_init_from_file_impl: failed to read tensor info
```

El quickstart oficial de PrismML apunta a su propio fork. Otros usuarios
reportan el mismo problema en LM Studio.

- Modelo: <https://huggingface.co/prism-ml/Bonsai-27B-gguf>
- Fork: <https://github.com/PrismML-Eng/llama.cpp>

---

## Opciones

### A) Modelo tool-capable que funcione hoy ← recomendada primero

Después de reiniciar, con la GPU andando, un modelo de 7-8B entrenado para tool
use vuela en 8 GB de VRAM.

**Ninguno de los que tenés sirve**: `meta-llama-3-8b-instruct` y
`deepseek-r1-distill-llama-8b` son los dos `trainedForToolUse: false`.

Hay que bajar uno que sí lo sea, y después:

```sh
cd ~/napps/reco-chat
RECO_CHAT_MODEL=<el-modelo-nuevo> python3 reco_chat.py
```

Si queda bien, cambiar el default en `reco_chat.py` (constante `MODEL`) o
exportar `RECO_CHAT_MODEL` en el `.zshrc`.

### B) Compilar el fork de PrismML (para usar bonsai de verdad)

```sh
git clone https://github.com/PrismML-Eng/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=ON && cmake --build build -j
./build/bin/llama-server \
  -m ~/.lmstudio/models/lmstudio-community/Bonsai-27B-GGUF/Bonsai-27B-Q1_0.gguf \
  --port 8080
```

Su `llama-server` también expone API compatible con OpenAI, así que **el chat
funciona sin cambiarle una línea**:

```sh
RECO_CHAT_BASE_URL=http://localhost:8080/v1 python3 reco_chat.py
```

Requisitos que **faltan** en la máquina:

- `nvcc` (CUDA toolkit) — no está instalado, son varios GB
- CUDA funcionando → depende del problema 1

### C) Esperar

A que LM Studio incorpore los kernels de PrismML. Sin fecha.

---

---

# Actualización 2026-08-13 (tarde): se decidió compilar el fork, no bajar otro modelo

Cambio de plan respecto a la sección de abajo: en vez de bajar Qwen3-8B/Ministral,
se va a compilar el fork de PrismML para usar **bonsai de verdad**. Ya liberaste
~10 GB borrando los modelos viejos (quedan solo bonsai + el embedding, 4.82 GB) y
quedan **33 GB libres**, así que hay margen.

## Lo que ya está hecho (yo lo hice, no hace falta repetirlo)

- **Repo clonado**: `~/napps/prismml-llama.cpp` (clon superficial, `--depth 1`,
  **187 MB** — mucho menos que la estimación anterior de ~370 MB, que era el
  tamaño con todo el historial).
- **Verificado en el código, no solo en el README**: en
  `ggml/include/ggml.h` este fork define `GGML_TYPE_Q1_0 = 41` (`GGML_TYPE_COUNT = 43`).
  Es exactamente el tipo de tensor 41 que hizo fallar la carga en LM Studio.
  Confirma que el diagnóstico de ayer era correcto y que este fork sí lo soporta.
- **Verificado que hay kernels CUDA reales para ese tipo**, no solo la
  definición: `ggml/src/ggml-cuda/template-instances/mmq-instance-q1_0.cu`,
  `mmq.cu`, `mmvq.cu`, `convert.cu` lo referencian. También hay un
  `mmq-hopper-q1.cu` — un camino optimizado específico para GPUs Hopper (H100).
  Tu RTX 2070 es Turing (compute capability 7.5), así que **no usaría ese
  camino optimizado**, pero el fork declara soporte explícito para 7.5 en
  `ggml/src/ggml-cuda/CMakeLists.txt` (`75-virtual` está en la lista default),
  así que debería andar por el camino genérico. Esto no lo pude probar de
  verdad porque acá no hay CUDA funcional para compilar y correr — es una
  lectura del código, no una corrida real.
- **No hace falta volver a bajar el `.gguf`**: el fork lee el mismo archivo que
  ya tenés en `~/.lmstudio/models/lmstudio-community/Bonsai-27B-GGUF/Bonsai-27B-Q1_0.gguf`
  (4.5 GB). Nada de descarga adicional por ese lado.

## Corrección importante sobre la versión de CUDA a instalar

**No instalar `cuda-toolkit` a secas** (te daría la 13.3, la última). Comprobé
en <https://docs.nvidia.com/cuda/> que **CUDA 13.x pide driver ≥ 580.65.06**, y
tu driver es **565.57.01**. Con la 13.3 compilaría bien (`nvcc` no necesita GPU
viva) pero **fallaría al ejecutar** el server por versión de driver insuficiente.

**Usar CUDA 12.6** en su lugar: pide driver ≥ 550, tu 565 lo cubre de sobra, y
es la versión 12.x más nueva disponible en el repo de NVIDIA que ya tenés
configurado (`12.6.3-1`).

## Cuánto ocupa (medido con `apt-cache show` real sobre los paquetes exactos,
## no una estimación genérica)

| Instalar | Tamaño real |
|---|---:|
| `cuda-toolkit-12-6` completo (61 paquetes: incluye nsight, cuda-gdb, docs) | **6.12 GB** |
| Solo lo necesario para compilar y correr (`nvcc`, `cudart-dev`, `cublas-dev`, `nvjitlink-dev`, `cccl`, `driver-dev`) | **1.71 GB** |
| Repo del fork (ya clonado) | 187 MB |
| Carpeta de build (objetos + binario) | ~0.5–1.5 GB, **estimado**, no compilé |

Recomendado: el paquete **mínimo** (1.71 GB) — nada de lo que se excluye
(nsight, cuda-gdb, documentación) hace falta para compilar ni para correr
`llama-server`. Total con margen: **~2.5–3.5 GB**, contra 33 GB libres.

## Todavía sigue roto: CUDA da error 999

No cambió desde ayer — **seguís sin reiniciar**. Compilar puede andar igual
(`nvcc` no necesita una GPU inicializada), pero **correr** el server sí la
necesita. Si tratás de arrancarlo antes de reiniciar, va a fallar o caer a CPU.

## Comandos, en orden

No tengo `sudo` con contraseña en este entorno, así que **estos los tenés que
correr vos**. Yo ya hice el `git clone` (paso 4 no hace falta repetirlo).

**1) Reiniciar** (en algún momento antes del paso 6; puede ser ahora o después
de instalar el toolkit, da igual):

```sh
sudo reboot
```

Después, verificar:

```sh
python3 -c "import ctypes; print(ctypes.CDLL('libcuda.so.1').cuInit(0))"   # tiene que dar 0
```

**2) Instalar el CUDA toolkit mínimo (12.6, no 13.x):**

```sh
sudo apt-get update
sudo apt-get install -y \
  cuda-nvcc-12-6 cuda-cudart-dev-12-6 cuda-driver-dev-12-6 \
  libcublas-dev-12-6 libnvjitlink-dev-12-6 cuda-cccl-12-6
```

**3) Agregar `nvcc` al PATH:**

```sh
echo 'export PATH="/usr/local/cuda-12.6/bin:$PATH"' >> ~/.zshrc
export PATH="/usr/local/cuda-12.6/bin:$PATH"
nvcc --version   # debe decir release 12.6
```

**4) ~~Clonar el fork~~ (ya hecho, está en `~/napps/prismml-llama.cpp`)**

**5) Compilar** (arquitectura fijada a 75 = Turing/RTX 2070, así no depende de
que la detección automática de GPU funcione; `LLAMA_CURL=OFF` porque no hace
falta bajar nada por HTTP, ya tenemos el `.gguf`):

```sh
cd ~/napps/prismml-llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75-real -DLLAMA_CURL=OFF
cmake --build build --config Release -j12 --target llama-server
```

(`-j12` porque tenés 12 hilos; el propio `--target llama-server` compila solo
el server, no todos los binarios de ejemplo, para no gastar de más.)

**6) Levantar el server**, apuntando al `.gguf` que ya tenés (nada de
descargar de nuevo), con todas las capas en GPU y usando el chat template que
trae el modelo (`--jinja`, necesario para que el tool calling salga bien
formado):

```sh
~/napps/prismml-llama.cpp/build/bin/llama-server \
  -m ~/.lmstudio/models/lmstudio-community/Bonsai-27B-GGUF/Bonsai-27B-Q1_0.gguf \
  --host 127.0.0.1 --port 8080 \
  -ngl 999 \
  --jinja
```

**7) Probar reco contra ese server** (en otra terminal):

```sh
cd ~/napps/reco-chat
RECO_CHAT_BASE_URL=http://localhost:8080/v1 python3 reco_chat.py
```

`reco_chat.py` manda cualquier valor en `RECO_CHAT_MODEL` (default
`prism-ml/bonsai-27b`) — con un solo modelo cargado, `llama-server` lo ignora
y usa el que tiene cargado, así que no hace falta tocar esa variable.

### Si algo de esto falla

- **`cmake` no encuentra `nvcc`**: confirmá el paso 3 (`nvcc --version`) antes
  de tocar `cmake`.
- **Error de compilación por falta de algún header CUDA**: probablemente falte
  algo del set mínimo; instalar el resto del meta-paquete completo
  (`sudo apt-get install -y cuda-toolkit-12-6`, los 6.12 GB) resuelve
  cualquier dependencia que se haya quedado afuera del recorte.
- **El server arranca pero `reco_chat.py` no logra tool calls bien
  formados**: probar sin `--jinja` (usa el formato interno de llama.cpp en vez
  del template del modelo) o revisar `~/napps/prismml-llama.cpp/tools/server/README.md`
  por flags de tool calling específicos de este fork.
- **Nada de esto lo probé corriendo de punta a punta** (no hay CUDA funcional
  acá para hacerlo) — a diferencia del resto de `reco-chat`, que sí está
  verificado. Si algo no coincide con lo que describe este documento, es lo
  primero a sospechar.

---

# Plan de modelos: quedarse con dos y liberar disco

Decisión tomada: **no** compilar el fork de PrismML (ocuparía varios GB más).
Entonces bonsai queda descartado y hay que elegir modelos que funcionen tal cual
en LM Studio.

## Situación de disco

```
/ ............ 466 GB, 420 usados, 26 libres  → 95% lleno
~/.lmstudio/models ... 14 GB
```

| Modelo actual | Tamaño | tool use | Veredicto |
|---|---:|---|---|
| `Meta-Llama-3-8B-Instruct` | 4.6 GB | ❌ `false` | modelo de 2024, ya quedó viejo |
| `DeepSeek-R1-Distill-Llama-8B` | 4.6 GB | ❌ `false` | es de razonamiento, no de agente |
| `Bonsai-27B` | 4.5 GB | ✅ pero **no carga** | **borrar**: inservible sin el fork |
| `DeepSeek-R1-Distill-Qwen-7B` | 0 B | — | carpeta vacía, restos; borrar |
| `nomic-embed-text-v1.5` | 84 MB | — | dejarlo, no molesta |

**Ninguno de los tres sirve para reco**: los dos que cargan son
`trainedForToolUse: false`.

## Recomendación

Bajar estos dos y borrar los tres actuales:

| Rol | Modelo | Tamaño | Por qué |
|---|---|---:|---|
| **Tool use (reco)** | `mistralai/ministral-3-8b` | ~5 GB | *"Native function calling and JSON output generation"* — hecho para esto |
| **Chat general + código** | `qwen/qwen3-8b` (Q4_K_M) | 5.03 GB | fuerte en código y matemática, "advanced agent capabilities", 100+ idiomas |

**Cuentas:** se borran 13.7 GB, se bajan ~10 GB → quedan ~3.7 GB libres de más,
y con dos modelos que sí sirven. Ambos entran cómodos en los 8 GB de VRAM de la
RTX 2070 (con CUDA arreglado), así que van a ir rápido.

### Alternativa si querés apretar más el disco

Qwen3-8B solo probablemente cubra **los dos roles** (chat, código y tool use).
Si arranca bien con reco, te ahorrás los ~5 GB de Ministral y quedás con un solo
modelo. Vale la pena probar en ese orden: primero Qwen3-8B para todo, y solo si
falla en tool calling, bajar Ministral.

### Descartados y por qué

- **Qwen3.6** (27B denso / 35B-A3B) — la generación nueva, pero no hay variante
  chica; ni de cerca entra en 8 GB de VRAM ni en el presupuesto de 10 GB.
- **Gemma 4 12B**, **Muse Glimmer 30B**, **DeepSeek V4 Flash** — todos por
  encima del límite de tamaño.
- **Ministral 3 14B reasoning** — pasada de tamaño para 8 GB de VRAM.
- **Ministral 3 3B** — entra de sobra, pero un 3B para tool calling sobre 19
  herramientas va a fallar como falló needle. No vale la pena el riesgo.

## Comandos

**Verificar antes de bajar** (importante: confirmá que diga tool use `true`
antes de gastar los GB — `lms get` muestra la ficha antes de confirmar):

```sh
lms get mistralai/ministral-3-8b
lms get qwen/qwen3-8b
```

**Borrar los viejos.** No existe `lms rm`; se hace desde la app (pestaña *My
Models* → botón de borrar) o directo:

```sh
rm -rf ~/.lmstudio/models/lmstudio-community/Bonsai-27B-GGUF                 # 4.5 GB
rm -rf ~/.lmstudio/models/lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF   # 4.6 GB
rm -rf ~/.lmstudio/models/lmstudio-community/DeepSeek-R1-Distill-Llama-8B-GGUF # 4.6 GB
rm -rf ~/.lmstudio/models/lmstudio-community/DeepSeek-R1-Distill-Qwen-7B-GGUF  # vacía
```

> Borrá **después** de confirmar que el modelo nuevo carga y responde, no antes.

**Confirmar que el nuevo está marcado para tool use:**

```sh
lms ls --json | python3 -c "
import json,sys
for m in json.load(sys.stdin):
    if m.get('type')=='llm':
        print(m['modelKey'], '->', m.get('trainedForToolUse'))
"
```

**Apuntar el chat al modelo nuevo:**

```sh
cd ~/napps/reco-chat
RECO_CHAT_MODEL=mistralai/ministral-3-8b python3 reco_chat.py
```

Si convence, cambiar la constante `MODEL` en `reco_chat.py` (línea 30, hoy dice
`"prism-ml/bonsai-27b"`) para que quede de default y el panel de `dev` lo tome solo.

> **Nota de honestidad:** los tamaños de Qwen3-8B están verificados contra los
> archivos publicados (Q4_K_M = 5.03 GB). El de Ministral 3 8B es estimado a
> partir de los 7 GB de "minimum system memory" que declara su ficha. Y la marca
> `trainedForToolUse` de LM Studio no la pude comprobar sin descargar: por eso el
> paso de verificar con `lms get` antes de bajar.

---

## Checklist para cuando vuelvas (plan activo: compilar el fork)

- [ ] `sudo reboot`
- [ ] `python3 -c "import ctypes; print(ctypes.CDLL('libcuda.so.1').cuInit(0))"` → ¿da `0`?
- [ ] `sudo apt-get update && sudo apt-get install -y cuda-nvcc-12-6 cuda-cudart-dev-12-6 cuda-driver-dev-12-6 libcublas-dev-12-6 libnvjitlink-dev-12-6 cuda-cccl-12-6`
- [ ] `echo 'export PATH="/usr/local/cuda-12.6/bin:$PATH"' >> ~/.zshrc && export PATH="/usr/local/cuda-12.6/bin:$PATH"`
- [ ] `nvcc --version` → ¿dice release 12.6?
- [ ] `cd ~/napps/prismml-llama.cpp && cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75-real -DLLAMA_CURL=OFF`
- [ ] `cmake --build build --config Release -j12 --target llama-server`
- [ ] Levantar el server (paso 6 más arriba) y dejarlo corriendo
- [ ] `RECO_CHAT_BASE_URL=http://localhost:8080/v1 python3 reco_chat.py` → ¿responde? ¿hace tool calls bien formados?
- [ ] Si el tool calling sale mal formado, probar sin `--jinja`
- [ ] Si conviene, poner el server a arrancar solo (systemd user service o agregarlo al panel `reco` de `dev`)
- [ ] Retomar la sesión: `claudep --resume 5628a003-b346-407e-a77b-90f2f5659571`

### Plan B (si el fork no anda o da demasiada guerra)

Volver al plan original: borrar bonsai (4.7 GB) y bajar un modelo chico que sí
cargue tal cual en LM Studio. Ver la sección **"Plan de modelos"** más abajo
para la comparación completa; en resumen:

```sh
lms get qwen/qwen3-8b          # ~5 GB, revisar ficha antes de confirmar
RECO_CHAT_MODEL=qwen/qwen3-8b python3 reco_chat.py
# si el tool calling no anda bien:
lms get mistralai/ministral-3-8b
```

---

## Nota sobre archivos sin commitear

`~/napps/nexus-private/projects.json` tiene cambios sin commitear — **algunos son
tuyos de antes** (`inmtec`, el `./dev.sh` de swd) y otros son de este trabajo
(quitar `needle`, agregar `reco`). No se commiteó nada.
