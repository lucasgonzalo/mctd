# req_1 — Sección de configuración del sistema

Estado: propuesto
Fecha: 2026-08-29
Archivo afectado principal: `app.py`

## 1. Contexto

Hoy todos los parámetros operativos están hardcodeados en `app.py`:

| Valor | Actual | Ubicación |
|---|---|---|
| Flags de glpsol | Fijos: `--model`, `--output`, `--log` | `app.py` (endpoint `/api/run`) |
| Timeout del proceso | 30 s | `TIMEOUT_SECONDS` |
| Ejecuciones guardadas por modelo | 5 | `RUNS_PER_MODEL` |
| Tipo de salida | Solución imprimible (`.txt`) + log crudo (`.log`) | endpoint `/api/run` |

No existe ningún mecanismo de configuración: ni archivo, ni endpoints, ni UI.

## 2. Objetivo y alcance

Centralizar la configuración operativa en un único archivo (`config.json`) editable
desde una sección de configuración del frontend, con validación en el backend.

Decisiones de alcance tomadas:

- **Configuración global única.** Los valores aplican a todas las ejecuciones de
  todos los modelos. No hay override por ejecución ni por modelo (ver §8).
- **Aplica al próximo run sin reiniciar** el server: la configuración se lee al
  momento de ejecutar cada resolución, no al arrancar.
- **Retrocompatible:** si `config.json` no existe o le faltan claves, se usan los
  defaults definidos acá. Los runs viejos guardados siguen renderizando igual.

## 3. Schema de configuración

Archivo: `config/config.json` (directorio dedicado con volumen propio de Docker,
misma estrategia que `models/` y `results/`: vive en el host, fuera del
contenedor). Un archivo suelto en la raíz habría requerido un bind-mount de
archivo que Docker convierte en directorio si no existe al arrancar.

### 3.1 `solver` — método de resolución

| Clave | Tipo | Default | Flag glpsol | Notas |
|---|---|---|---|---|
| `method` | enum: `simplex` \| `interior` | `simplex` | `--simplex` / `--interior` | `interior` solo LP |
| `simplex_variant` | enum: `primal` \| `dual` | `primal` | `--primal` / `--dual` | Ignorado si `method=interior` |
| `presolve` | bool | `true` | `--presol` / `--nopresol` | |
| `exact_check` | bool | `false` | `--xcheck` | Verifica la base final con aritmética exacta |
| `seed` | null \| entero | `null` | `--seed <n>` | Para modelos MathProg con aleatoriedad (`Uniform`, etc.); `null` = no pasar flag. Fijarla hace runs reproducibles |
| `tmlim` | entero (segundos) | `25` | `--tmlim <n>` | Límite **blando**: glpsol corta y devuelve la mejor solución parcial |
| `memlim` | null \| entero (MB) | `null` | `--memlim <n>` | `null` = sin límite |

### 3.2 `mip` — opciones de enteros

| Clave | Tipo | Default | Flag glpsol | Notas |
|---|---|---|---|---|
| `relax` | bool | `false` | `--nomip` | Resuelve el MIP como LP relajado (variables enteras → continuas) |
| `mipgap` | null \| float > 0 | `null` | `--mipgap <tol>` | Gap relativo de optimalidad |
| `cuts` | bool | `false` | `--cuts` | Gomory + MIR + cover + clique |

### 3.3 `output` — tipo de salida

| Clave | Tipo | Default | Notas |
|---|---|---|---|
| `sensitivity` | bool | `true` | Activa `--ranges <archivo>` (análisis de sensibilidad). **Solo válido con simplex** (ver §4) |
| `plain_solution` | bool | `true` | Activa `-w <archivo>`: solución en formato plano machine-readable, insumo del modo `pretty` |
| `mode` | enum: `raw` \| `pretty` \| `both` | `pretty` | `raw` = texto plano como hoy; `pretty` = tablas parseadas (estado, valor objetivo, actividad y marginales por variable/restricción); `both` = ambas vistas |

> Nota de implementación: el parser del modo `pretty` lee la solución imprimible
> (`.txt`, columnas alineadas por la línea de guiones) y no el `.sol` plano,
> porque el imprimible incluye los **nombres** de variables y restricciones,
> mientras que `-w` solo trae índices (`i 1`, `j 1`). El `.sol` se sigue
> generando como artefacto descargable/para scripts.

Archivos por run (en `results/`, siguiendo el patrón actual
`{modelo}-{stamp}.{ext}`):

- `.txt` — solución imprimible (como hoy, `--output`)
- `.sol` — solución plana (`-w`), si `plain_solution=true`
- `.rng` — reporte de sensibilidad (`--ranges`), si `sensitivity=true`
- `.log` — log (como hoy)

Todos sujetos a la misma poda de historial circular (`runs_per_model`).

### 3.4 `runtime` — operación del proceso

| Clave | Tipo | Default | Notas |
|---|---|---|---|
| `timeout_seconds` | entero (5–600) | `30` | Kill **duro** del subprocess. Debe superar a `tmlim` (ver §4) |
| `runs_per_model` | entero ≥ 0 | `5` | Historial circular por modelo; `0` = ilimitado |

### 3.5 `editor` — editor web

| Clave | Tipo | Default | Notas |
|---|---|---|---|
| `template` | string | Contenido del placeholder actual de "Nuevo" (ejemplo mínimo `var x >= 0; ...`) | Contenido inicial de un modelo nuevo |

## 4. Reglas de validación (backend, respuesta 400 con detalle por campo)

1. **`method=interior` + `sensitivity=true` es inválido** — `--ranges` solo
   aplica con simplex. Mensaje claro, no degradación silenciosa.
2. **`tmlim + 5 <= timeout_seconds`** — el límite blando debe dar margen al
   proceso para escribir su salida parcial antes del kill duro.
3. `timeout_seconds` ∈ [5, 600]; `runs_per_model >= 0`; `tmlim >= 1`.
4. `mipgap > 0` (si no es `null`); `memlim > 0` (si no es `null`); `seed` entero ≥ 0 (si no es `null`).
5. **Enums estrictos** — solo valores del schema; claves desconocidas → 400
   (un typo no debe perderse en silencio).
6. **Prohibido cualquier campo de flags/argv libres.** La configuración es un
   schema tipado que el backend traduce a argv; texto libre sería inyección de
   comandos directa sobre `subprocess`.

## 5. API

| Endpoint | Comportamiento |
|---|---|
| `GET /api/config` | Devuelve la config vigente (archivo fusionado con defaults). Nunca 404: sin archivo → defaults completos |
| `PUT /api/config` | Valida §4, escribe `config.json` atómicamente (tmp + rename), devuelve la config guardada. Inválido → 400 con `{field: mensaje}` |

`POST /api/run` (modificación):

- Construye argv desde la config vigente leída **en el momento del run**.
- La respuesta agrega: contenido del `.sol` y `.rng` cuando correspondan, y
  `command`: el argv resuelto efectivamente ejecutado (transparencia y debug).

## 6. Frontend

- **Sección de configuración** accesible desde el encabezado (la forma exacta —
  modal o panel — queda a diseño) con los grupos §3, edición y guardado vía
  `PUT /api/config`. Al guardar muestra confirmación; aplica al próximo run.
- **Tab "Sensibilidad"** en el panel de resultados, visible solo cuando el run
  produjo `.rng`.
- **Modo `pretty`:** render de tablas parseadas desde `.sol` (una tabla para
  variables, otra para restricciones: actividad, costo reducido / dual, estado).
  `raw` mantiene el `<pre>` actual. `both` ofrece ambas vistas.
- **Historial de runs:** la vista de un run restaura también `.sol`/`.rng`
  cuando existan (mismo mecanismo que hoy para `.txt`/`.log`).

## 7. Nice to have (opcional, fuera del núcleo)

- **Acción "Validar" en el editor:** ejecuta glpsol con `--check` (parsea el
  modelo sin resolver) y reporta errores de sintaxis rápido. No es parte del
  schema de configuración: es una acción fija del editor.

## 8. Fuera de alcance (explícito)

- Override de configuración por ejecución o por modelo.
- Migrar de CLI (`glpsol`) a la API C de GLPK (`swiglpk` o similar).
- Configuración multiusuario o autenticación.
- Cambios en los formatos de archivo de `models/` o del historial existente.
