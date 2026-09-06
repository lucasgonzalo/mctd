# req_3 — Organización de modelos en carpetas (árbol de archivos)

Estado: implementado
Fecha: 2026-09-05
Archivos afectados principales: `app.py`, `static/index.html`,
`static/guide.html`

## 1. Contexto

Hoy el nombre de un modelo (`NAME_RE = ^[A-Za-z0-9_-]+$`, plano, sin `/`) es
LA identidad del sistema en cuatro planos simultáneos:

1. Archivo: `models/{name}.mod`
2. Resultados: `results/{name}-{stamp}.{txt,log,sol,rng}`, re-parseados desde
   el filename (`RUN_FILE_RE`, `run_files_for`, `all_runs`) para reconstruir
   el historial
3. URLs: `/api/models/{name}`, `/api/runs/{name}/{raw}`
4. Estado del frontend: input de nombre, `currentRunKey`, highlight del sidebar

Permitir carpetas = permitir `/` en el nombre = los cuatro planos aprenden a
la vez. No hay bloqueo técnico: es una decisión de diseño con costo de
reversión alto si se elige mal (cada archivo histórico de `results/`
referencia el esquema).

Base ya resuelta por v0.4.0: el endpoint de rename ya mueve modelo + meta +
runs juntos (la mecánica de "el historial sigue al modelo"), y el sidebar ya
tiene drag & drop externo, menú ⋮ y modales validados.

## 2. Objetivo y alcance

Organizar modelos en una jerarquía de carpetas visible como árbol en el
sidebar, con move de archivos por drag & drop interno.

Decisiones de alcance tomadas:

- **Anidamiento hasta profundidad 5** (suficiente para la cursada; el backend
  lo valida, no lo infiere).
- **Carpetas vacías permitidas**: una carpeta es un directorio real de
  `models/`; puede quedar vacía tras mover/borrar su contenido.
- **El botón "+ Nuevo" sigue creando en la raíz**; organizar es mover
  después. (Crear-en-carpeta-seleccionada queda para fase 3.)
- **Mover una carpeta mueve todo su contenido** (modelos + meta + runs), con
  la misma mecánica del rename de v0.4.0 un nivel arriba.
- **Eliminar carpeta elimina recursivamente** modelos y runs adentro, con
  confirmación que muestra la cantidad de modelos afectados.
- Sin carpetas virtuales en base de datos ni renombrado masivo: el filesystem
  es la fuente de verdad, como hoy.

## 3. Magnitud del cambio

### 3.1 Qué queda intacto

- Tubería de runs, stamps, historial circular, poda: agnóstica del path.
- glpsol: `--model models/tp1/ej3.mod` es un path más; `ERROR_LINE_RE`
  (`^\S*\.mod:`) ya soporta rutas con subdirectorios (verificado).
- Editor, resaltado, marcado de errores, panel de resultados.
- Validación por segmento: cada segmento del path sigue siendo `NAME_RE`.

### 3.2 Qué se modifica de verdad

| Pieza | Impacto |
|---|---|
| `validate_name` | Pasa a validar path: split `/`, `NAME_RE` por segmento, sin vacíos/`.`/`..`, profundidad ≤ 5, largo total ≤ 120 |
| `run_files_for` / `all_runs` / `prune_runs` | `results/` espeja el árbol: `results/tp1/ej3-{stamp}.*`; lookup por glob de prefijo exacto, listado global con `rglob` |
| `run_model` | `mkdir -p` del subdirectorio de `results/` antes de ejecutar glpsol |
| `created_at` / meta | `.meta` espeja el árbol: `models/.meta/tp1/ej3.json`; el walk de `list_models` excluye `.meta` |
| Rutas de API | `{name}` no matchea `/` en FastAPI: ver §7 |
| Rename v0.4.0 | Se generaliza a move (modelo→modelo, modelo→carpeta, carpeta→carpeta) |
| Frontend sidebar | Lista plana → árbol colapsable con DnD interno |

### 3.3 Riesgos puntuados

1. **Esquema de resultados (mitigado por diseño):** la propiedad "nombre
   plano = profundidad 0" hace que los archivos históricos planos sigan
   siendo válidos sin migración. El riesgo queda acotado a implementar mal
   el glob/rglob, no al esquema.
2. **Race con corrida activa:** mover/renombrar durante un run de ese modelo
   deja resultados huérfanos. El rename v0.4.0 ya bloquea con 409; move
   hereda el guard para el modelo y para cualquier modelo dentro de la
   carpeta movida.
3. **Rutas de dos segmentos en FastAPI:** `{name:path}` solo es confiable al
   final de la ruta. Las rutas compuestas pasan a query/body (§7). Papercute
   conocido; decisión explícita para no descubrirlo a mitad de implementación.
4. **DnD interno confundido con el externo:** ya hay drop de archivos del SO
   sobre el sidebar. El interno (mover dentro del árbol) se distingue por
   `dataTransfer` types: archivos del SO vs dato interno con tipo propio.

### 3.4 Estrategia: fases sobre main

Cambio de frontend y backend acotado, sin tocar Docker ni pipeline central.
Fase 1 (backend) es retrocompatible al 100% y desplegable sola; fase 2
(frontend) la consume. No requiere branch dedicada (a diferencia de req_2):
si fase 1 queda sola en main, nada se rompe.

## 4. Formas posibles de hacerlo (análisis de alternativas)

| Alternativa | Veredicto | Razón |
|---|---|---|
| **A. Espejar el árbol en `results/` y `.meta/`** | **Elegida** | Nombre plano = profundidad 0: cero migración de datos existentes; lookup por glob exacto sin ambigüedad de parsing |
| B. Aplanar el path en el filename (`tp1__ej3`) | Rechazada | Colisiona con nombres legales de hoy (`_` ya está permitido en `NAME_RE`); parsing ambiguo |
| C. Carpetas virtuales en un índice (JSON/DB) | Rechazada | Dos fuentes de verdad (fs + índice) para evitar un `rglob`; complejidad de sincronización gratuita |
| D. Carpetas visuales por prefijo (`tp1-` → grupo) | Rechazada | No es jerarquía real, colisiona con `-` legal, no resuelve mover nada |

## 5. Arquitectura (alternativa A)

```
models/tp1/ej3.mod              ← el modelo
models/tp1/vacios/              ← carpeta vacía: válida y visible
models/.meta/tp1/ej3.json       ← fecha de creación (espeja el árbol)
results/tp1/ej3-20260905-215603.txt|.log|.sol|.rng   ← espeja el árbol
```

- **Identidad:** un modelo es un path relativo (`tp1/ej3`); la raíz es el
  caso particular de un solo segmento. Todo el código existente que opera
  sobre nombres planos sigue funcionando para modelos en raíz sin cambios de
  comportamiento.
- **Lookup de runs (sin re-parsear):** `run_files_for("tp1/ej3")` hace glob
  de `results/tp1/ej3-*.{ext}` y extrae el stamp con sufijo regex
  (`-(\d{8}-\d{6})$`). Determinista incluso con nombres feos: el modelo
  `a-20260101-010101` produce `a-20260101-010101-{stamp}` y el strip del
  último sufijo es único.
- **Listado global (`all_runs`):** `rglob` sobre `results/`; modelo =
  ruta relativa sin el sufijo `-{stamp}.{ext}`.

## 6. Validación de nombres (paths)

`validate_path(name)`:

1. Split por `/`: 1 a 5 segmentos, ninguno vacío (`a//b` → 400).
2. Cada segmento pasa `NAME_RE` (`^[A-Za-z0-9_-]+$`) — esto ya elimina
   `.` y `..` (los puntos están prohibidos).
3. Largo total ≤ 120 caracteres.
4. Mensajes de error en español, mismo estilo que los existentes.

Las carpetas heredan la misma validación (son paths de directorio).

## 7. API

Rutas con `{name:path}` SOLO al final; las compuestas van por query/body:

| Endpoint | Cambio |
|---|---|
| `GET /api/models` | Devuelve árbol: `{folders: [...paths], models: [{name, created}]}` (name = path completo) |
| `GET /api/models/{name:path}` | Igual que hoy; `name` puede llevar `/` |
| `POST /api/models` | Body `{name, content}`; name = path (valida `validate_path`); crea subdirectorios si no existen |
| `DELETE /api/models/{name:path}?type=model\|folder` | Modelo: como hoy. Carpeta: borrado recursivo; responde cantidad de modelos y runs eliminados |
| `POST /api/models/move` | **Reemplaza el rename de v0.4.0**. Body `{from, to, type: "model"\|"folder"}`: rename de modelo, move a carpeta, move/renombrado de carpeta entera. Guard 409 contra corrida activa de cualquier modelo afectado |
| `POST /api/models/folders` | Body `{path}`: crea carpeta (validada, padres incluidos) |
| `GET /api/runs/{name:path}` | Como hoy, con `/` |
| `GET /api/runs?model={path}&raw={stamp}` | Pasa de `/api/runs/{name}/{raw}` (dos segmentos + slash no combinan) |
| `POST /api/run` | Sin cambios de contrato; `name` ahora es path |

## 8. Frontend

- **Árbol colapsable** en `#files-section`: carpetas primero (orden
  alfabético), modelos después (por fecha de creación como hoy). Estado de
  colapso en memoria por sesión (persistencia en localStorage: fase 3).
- **DnD interno:** arrastrar un modelo sobre una carpeta (o la raíz) =
  `POST /api/models/move`. Se distingue del drop externo (archivos del SO)
  por el tipo de `dataTransfer`. Hover sobre carpeta colapsada durante ~600ms
  la expande.
- **Menú ⋮ extendido** (el de v0.4.0): modelos agregan "Mover a…" (modal con
  selector de carpeta como fallback del drag); carpetas tienen menú propio:
  renombrar, eliminar (confirmación con conteo), nueva subcarpeta.
- **Botón "Nueva carpeta"** al pie del sidebar junto a "+ Nuevo".
- El input de nombre de la toolbar acepta paths (`tp1/ej3`); el validador
  cliente aplica las mismas reglas por segmento.
- `guide.html`: sección corta sobre carpetas y move.

## 9. Fases

1. **Backend:** `validate_path`, espejo en `results/` + `.meta/` (con mkdir
   en run), `rglob` en all_runs, endpoints move/folders/delete-folder,
   rutas `:path` y query-param de runs. 100% retrocompatible desplegado solo.
2. **Frontend:** árbol, DnD interno, menús extendidos, "Nueva carpeta".
3. **Extras opcionales:** crear-en-carpeta-seleccionada, colapso persistente
   en localStorage, DnD de carpetas sobre carpetas.

## 10. Fuera de alcance (explícito)

- Permisos o carpetas por usuario (app single-user local).
- DnD para reordenar manualmente (el orden es carpeta → fecha de creación).
- Tags/labels como alternativa u complemento a carpetas.
- Migración de modelos existentes: la raíz es un lugar válido para siempre.
