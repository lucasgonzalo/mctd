# req_2 — Selector de motor de resolución (GLPK | HiGHS | CBC)

Estado: propuesto
Fecha: 2026-08-29
Archivos afectados principales: `app.py`, `static/index.html`, `Dockerfile`,
`compose.yaml`

## 1. Contexto

Hoy el pipeline de resolución está casado con glpsol de punta a punta: glpsol
traduce MathProg, resuelve y genera los tres artefactos (`.txt`, `.sol`, `.rng`).

Motivación: GLPK es un LP didáctico correcto pero un MIP débil (branch-and-cut
básico, sin heurísticas modernas). HiGHS es el solver open source de referencia
actual y puede ser órdenes de magnitud más rápido en MIP difíciles. La sintaxis
de modelado (MathProg) **no cambia**: es requisito de la cursada.

Restricción dura verificada: **solo GLPK traduce MathProg**. HiGHS y CBC leen
formato LP/MPS, no `.mod`. Cualquier motor alternativo exige una tubería de dos
etapas.

Disponibilidad de paquetes (verificado contra los índices de Debian):

| Paquete    | bookworm (`python:3.12-slim` actual) | trixie |
|------------|---------------------------------------|--------|
| coinor-cbc | 2.10.8                                | 2.10.12 |
| highs      | **no existe**                         | 1.10.0 |

## 2. Objetivo y alcance

Agregar a la configuración global un **motor de resolución** seleccionable,
manteniendo MathProg como único lenguaje y la UI/editor intactos en estructura.

Decisiones de alcance tomadas:

- **Configuración global única** (misma filosofía que req_1 §8): no hay motor
  por modelo ni por ejecución.
- **MVP con HiGHS** como segundo motor; CBC entra en fase 2 (una vez que existe
  la abstracción, agregarlo es trivial).
- GLPK sigue siendo el default y el único motor con análisis de sensibilidad.

## 3. Magnitud del cambio: qué significa abordarlo

**Este requerimiento NO es un cambio incremental.** A diferencia de req_1 (que
agregó configuración sobre un pipeline intacto), este modifica el núcleo de
ejecución de la aplicación. Antes de comenzar conviene tener claro el impacto:

### 3.1 Qué queda intacto

- El lenguaje y todos los modelos existentes (`.mod`): cero migración.
- La tubería de runs: stamps, historial circular, poda, `results/`, API de
  modelos. Es agnóstica del motor.
- El editor, el resaltado y el marcado de errores: los errores de sintaxis los
  sigue reportando glpsol (etapa de traducción) en el mismo formato
  `modelo.mod:LINEA: msg` que `parse_glpk_error` ya consume.

### 3.2 Qué se modifica de verdad

| Pieza | Impacto |
|---|---|
| `POST /api/run` | Pasa de un subprocess a una tubería de 2 etapas con bifurcación por motor |
| `build_argv()` | Refactor a estrategia por motor (`build_glpsol_argv` / traducción + `build_highs_argv`) |
| Schema de config | Nueva clave `solver.engine` + reglas de validación cruzadas por motor |
| `parseSolution()` (modo pretty) | Solo parsea el formato imprimible de glpsol; HiGHS requiere vista raw primero y parser propio después (fase 2) |
| Pestaña "Sensibilidad" | No existe en HiGHS/CBC: ocultarla condicionalmente |
| **`Dockerfile`** | **Cambio de base de imagen (bookworm → trixie) e instalación de `highs`.** Es el punto de mayor riesgo: puede afectar el despliegue actual aunque nunca se seleccione el motor nuevo |

### 3.3 Riesgos puntuados

1. **Base Docker:** cambiar de distro base puede arrastrar versiones nuevas de
   Python/glpk/fastapi. Hay que validar el build completo y un run GLPK antes
   de tocar el pipeline.
2. **Formatos de salida distintos por motor:** la vista raw funciona igual (es
   texto plano), pero las tablas bonitas y el archivo `.sol` plano solo existen
   para GLPK. Hay que decidir por run qué artefactos existen y renderizar sin
   asumir que todos están.
3. **Doble timeout:** la etapa de traducción y la de resolución comparten el
   presupuesto de `timeout_seconds`; hay que repartirlo para que una
   traducción lenta no se coma el tiempo de resolución.
4. **Comportamiento con `display`/`printf`:** con motor ≠ GLPK los comandos de
   post-proceso de MathProg no se ejecutan (los interpreta glpsol post-solve).
   La salida pasa a ser el listado de solución del motor. Debe quedar
   documentado en la guía para que no sorprenda en pleno TP.

### 3.4 Estrategia: branch dedicada

**Este cambio se desarrolla y prueba en una branch separada, no en `main`.**
Sugerencia de nombre: `feat/motor-resolucion`.

Razones:

- `main` debe seguir siendo desplegable para la cursada en todo momento; el
  cambio toca la base del Docker image y el pipeline central de `/api/run`,
  o sea las dos cosas que no se pueden romper.
- La branch permite comparar salidas GLPK vs HiGHS con los mismos modelos
  (mismo `.mod`, dos runs, diff de resultados) antes de mergear.
- El rollback de un experimento de infraestructura (base de imagen) es un
  `git checkout main`, no un revert de emergencia.

Criterio de merge: un run GLPK en la branch produce artefactos idénticos a
`main` (regresión cero) + al menos un modelo TP resuelto con HiGHS con
solución verificada contra GLPK.

## 4. Formas posibles de hacerlo (análisis de alternativas)

| Alternativa | Veredicto | Razón |
|---|---|---|
| **A. Tubería 2 etapas: glpsol traduce → motor CLI resuelve** | **Elegida** | Reusa toda la tubería de runs; MathProg intacto; errores de sintaxis siguen saliendo de la etapa 1 en el mismo formato |
| B. Modo Pyomo + HiGHS en el IDE | Rechazada | Es una segunda app: otro lenguaje en el editor, ejecución de Python arbitrario desde la web (seguridad), sin `--ranges` out-of-the-box, reporting por escribir |
| C. WASM en el navegador (estilo glpk.js) | Rechazada | Cambia el producto (client-side); HiGHS/CBC no son el objetivo ahí; se pierde el historial persistente |
| D. Envío a NEOS (remoto) | Rechazada | No es self-hosted ni local; dependencia de red y de un servicio externo |

## 5. Arquitectura de la tubería (alternativa A)

```
etapa 1 (traducción):  glpsol --model X.mod --wlp X.lp --check   ← no resuelve
etapa 2 (resolución):  highs X.lp --solution_file X.txt --log_file X.log
```

- Los errores de sintaxis de MathProg los atrapa la etapa 1 con el formato
  `modelo.mod:LINEA: msg` → `parse_glpk_error` y el gutter rojo siguen
  funcionando sin cambios.
- La etapa 2 hereda el timeout duro (`timeout_seconds`); el límite blando se
  mapea por motor (§6).
- Si `engine=glpk`: una sola etapa, comportamiento exacto al actual.

## 6. Cambios en el schema de configuración

Nueva clave `solver.engine`: `glpk` (default) | `highs` | `cbc`.

Mapeo de opciones comunes por motor:

| Clave config | glpsol | highs | cbc |
|---|---|---|---|
| `tmlim` | `--tmlim` | `--time_limit` | `-sec` |
| `mipgap` | `--mipgap` | `--mip_rel_gap` | `-ratio` |
| `presolve` | `--nopresol` | `--presolve off` | `-presolve off` |
| `method` (simplex/interior) | `--interior` / `--dual` | GLPK-only | GLPK-only |
| `exact_check`, `seed`, `memlim`, `cuts` | flags propios | GLPK-only | GLPK-only |
| `sensitivity` | `--ranges` | **no existe** | **no existe** |

Reglas de validación nuevas (extienden req_1 §4):

1. `engine` ∈ {`glpk`, `highs`, `cbc`}; otro valor → 400.
2. `sensitivity=true` + `engine != glpk` → 400 (mismo criterio que
   interior+sensitivity: mensaje claro, no degradación silenciosa).
3. Opciones GLPK-only con `engine != glpk`: se **ignoran** (no hay error de
   validación) pero la UI las oculta, y `command` en la respuesta del run
   muestra el argv real ejecutado — transparencia ya existente.

## 7. API

`POST /api/run` (modificación):

- Si `engine=glpk`: comportamiento actual, sin cambios.
- Si no: etapa 1 + etapa 2; la respuesta agrega `engine` y los archivos del
  run siguen el patrón `{modelo}-{stamp}.{ext}` (`.lp` traducido, `.txt`
  solución del motor, `.log` combinado).
- `GET /api/runs/{name}/{raw}` y `GET /api/models/{name}` restauran los
  artefactos nuevos con el mismo mecanismo actual.

## 8. Frontend

- **Selector de motor** en el modal de configuración (grupo `solver`).
- Ocultar dinámicamente: pestaña "Sensibilidad" y campos GLPK-only cuando
  `engine != glpk`.
- **Badge de motor** en el run header ("GLPK" / "HiGHS").
- `parseSolution()` (modo pretty) parsea el formato imprimible de glpsol:
  con motor ≠ GLPK el fallback a vista raw ya existe (`rebuildTabs`); el
  parser bonito del formato HiGHS es fase 2.

## 9. Docker / infraestructura

- Base `python:3.13-slim` (trixie) — o `python:3.12-slim-trixie`.
- `apt-get install glpk-utils highs` (+ `coinor-cbc` en fase 2).
- `compose.yaml`: sin cambios de volúmenes; sí cambia el build de la imagen
  (ver riesgo §3.3.1).

## 10. Fases

1. **MVP:** branch dedicada (§3.4), selector GLPK|HiGHS, tubería 2 etapas,
   salida raw para HiGHS, validaciones §6, Docker trixie. Refactor:
   `build_argv` → estrategia por motor.
2. **Fase 2:** parser pretty del formato de solución de HiGHS + motor CBC.
3. **Fase 3 (opcional):** badge de motor en el historial de runs.

## 11. Fuera de alcance (explícito)

- Modo Pyomo u otro lenguaje de modelado (ver §4-B).
- Ejecución remota (NEOS u otros servicios).
- Motor por modelo o por ejecución.
- Análisis de sensibilidad con motores no-GLPK (no soportado por los CLIs).
