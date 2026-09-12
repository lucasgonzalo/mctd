# req_4 — Mejoras de UI por etapas (atajos, indicadores, copiado, feedback)

Estado: en implementación — etapas 1–2 implementadas (2026-09-06), etapa 3
descartada por decisión del usuario, etapas 4–6 pendientes.
Fecha: 2026-09-06
Archivos afectados principales: `static/index.html`, `static/guide.html`,
`app.py` (solo etapa 6)

## 1. Contexto

Relevamiento de mejoras simples de UI (verificado contra el código de
`static/index.html` v0.6.0, 1659 líneas — nada de esto existe hoy):

| N.º | Mejora | Verificado en |
|---|---|---|
| 1 | `Ctrl+S` guardar, `Ctrl+Enter` resolver | Único atajo global: Escape cierra modales (l. 1329) |
| 2 | Indicador visual de cambios sin guardar | `isDirty()` existe (l. 1459) pero solo lo consumen el guard y `beforeunload`; no hay señal en pantalla |
| 3 | Posición `Ln/Col` del cursor | `#status-line` solo muestra mensajes de run (l. 1581) |
| 4 | `Ctrl+/` comentar/descomentar líneas | El editor solo intercepta Tab (l. 1640) |
| 5 | Zoom de fuente (`Ctrl+rueda`) | Tamaños fijos en CSS (l. 129, 137) |
| 6 | Botón "Copiar" de resultados | No existe; las tablas se renderizan en `renderTables` (l. 700) |
| 7 | Punto de estado (ok/error) por ejecución en el historial | `all_runs` devuelve `{model, raw, stamp}` — sin estado (app.py l. 341); el exit code solo vive en la respuesta del run vivo, los restaurados tienen `exit_code: None` (app.py l. 485) |
| 8 | Chip de `z` + estado en el encabezado del run | `parseSolution` ya extrae `info.Status` y `info.Objective` (l. 670), pero `#run-header` es texto plano (l. 1578) |
| 9 | Filtro de modelos en el sidebar | El árbol se renderiza completo siempre (`renderTreeLevel`, l. 894) |
| 10 | Toasts en vez de `alert()` | ~9 llamadas a `alert()` en errores de guardar/resolver/exportar/importar |

## 2. Objetivo y alcance

Implementar los 10 items agrupados en **6 etapas desplegables de forma
independiente**, ordenadas por relación valor/riesgo. Cada etapa es un commit
coherente por sí misma; ninguna depende de otra salvo lo explícitamente
indicado (E4 y E5 se refuerzan mutuamente pero funcionan solas).

| Etapa | Items | Toca backend | Riesgo |
|---|---|---|---|
| 1. Atajos de teclado | 1, 4 | No | Bajo |
| 2. Indicadores en vivo | 2, 3, 8 | No | Bajo |
| 3. Zoom de fuente | 5 | No | Medio (invariante de alineación) |
| 4. Copiar resultados | 6 | No | Bajo |
| 5. Filtro + toasts | 9, 10 | No | Bajo-medio |
| 6. Estado persistido por run | 7 | **Sí** (app.py) | Medio |

Decisiones de alcance tomadas:

- **Vanilla JS single-file:** ninguna mejora agrega dependencias ni build.
  Todas las de la 1–5 respetan la arquitectura textarea + overlay.
- **Multi-cursor queda afuera** (requiere CodeMirror/Monaco — req futuro).
- **Preferencias de vista en `localStorage`, no en `config.json`:** el zoom es
  estado del navegador, no operación del solver. `config.json` es global y
  compartida; el zoom es por máquina/dónde se proyecta.

## 3. Invariantes del sistema (leer antes de implementar cualquier etapa)

Trampas verificadas en el código actual. Romperlas es el bug más probable de
cada etapa:

1. **Alineación gutter ↔ overlay ↔ error.** La correspondencia visual depende
   de que `#gutter .ln`, `#editor` y `#highlight` compartan **line-height de
   20px exacto** (CSS l. 129, 132, 137). Además el scroll a línea con error
   **hardcodea el 20**: `$('editor').scrollTop = ... * 20` (l. 1551). Toda
   etapa que toque tipografía (solo E3) debe actualizar los TRES usos.
2. **`savedContent` tiene 6 sitios de asignación** (l. 1045, 1079, 1281,
   1503, 1518, 1538 — el relevamiento inicial contó 5 y se comió el de
   `openModel`: la deriva que esta regla existía para evitar). El indicador
   de sucio (E2) se vuelve inconsistente si aparece un séptimo sitio sin
   avisar. Mitigación aplicada en E2: `setSavedContent()` obligatorio.
3. **`#status-line` tiene un solo escritor** (`render()`, l. 1581). No
   agregarle segundos escritores (por eso Ln/Col vive en su propia barra, E2).
4. **Clipboard API exige secure context.** `navigator.clipboard` funciona en
   `localhost` pero **no** en `http://<ip-de-lan>` — caso real de un contenedor
   Docker accedido desde otra máquina. E4 necesita fallback.
5. **`static/` está COPY'd en la imagen** (no hay volumen para él): cada etapa
   requiere `docker compose up -d --build` para llegar al navegador.
6. **La cirugía directa de `.value` rompe el undo nativo** del textarea
   (precedente aceptado: el handler de Tab, l. 1644). Ver alternativa en E1.
7. **Nombres ASCII:** `NAME_RE = ^[A-Za-z0-9_-]+$` (app.py l. 46). El filtro
   (E5) no necesita normalizar acentos — los nombres no pueden tenerlos.

## 4. Etapas

### Etapa 1 — Atajos de teclado (items 1 y 4)

**Qué:** `Ctrl+S`/`⌘S` guarda, `Ctrl+Enter` resuelve, `Ctrl+/` comenta o
descomenta las líneas de la selección.

Diseño:

- Listener global de `keydown` junto al de Escape (l. 1329). Guard de scope:
  si algún overlay está abierto (`.open` en `#modal-overlay`,
  `#config-overlay`, `#rename-overlay`, `#move-overlay`, `#unsaved-overlay`),
  ignorar — los modales tienen sus propios Enter/Escape.
- `Ctrl+S` → `saveCurrentModel()` (l. 1509, devuelve bool) + `preventDefault`
  (evita el diálogo de guardar página).
- `Ctrl+Enter` → `$('btn-run').click()`. **No duplicar la lógica:** el
  handler ya deshabilita el botón durante el run (l. 1527), y pasar por
  `.click()` hereda ese guard anti-doble-disparo gratis.
- `Ctrl+/`: activo solo con foco en `#editor`. Sobre las líneas que abarca la
  selección (o la línea del caret si no hay selección): si **todas** las líneas
  no vacías empiezan (tras indentación) con `#` → quitar el `# ` ; si no →
  anteponerlo después del indent. Restaurar la selección cubriendo las líneas
  afectadas y llamar `refreshEditor()` para recolorear.

Decisiones y tradeoffs:

| Fork | Decisión | Razón |
|---|---|---|
| Mutación via `.value` (precedente Tab) vs `document.execCommand('insertText')` | **`execCommand` si es aplicable**; si la operación lo complica, caer a `.value` documentando la pérdida de undo | `insertText` **preserva el undo nativo** (Ctrl+Z sigue funcionando); la API está deprecada pero implementada en todos los navegadores y no requiere build |
| `Ctrl+Enter` reusa `.click()` vs refactor a función nombrada | `.click()` | Cero refactor; el estado disabled del botón es el guard ya probado |
| Modificador en Mac (`metaKey`) | Aceptar `ctrlKey \|\| metaKey` | Costo nulo, evita el atajo muerto en Safari |

Borde conocido: mientras existan `alert()` (hasta E5), los errores de
`Ctrl+S` son bloqueantes — aceptable interinamente.

Verificación:

- [ ] `Ctrl+S` con modelo sucio guarda y refresca el sidebar; sin nombre
      muestra el mismo error que el botón.
- [ ] `Ctrl+Enter` no dispara un segundo run si se pulsa durante uno activo.
- [ ] `Ctrl+/` con selección multilínea alterna (comenta → descomenta),
      respeta indentación y conserva la selección; `Ctrl+Z` deshace.
- [ ] Con la configuración abierta, ningún atajo dispara.

**Entregable extra:** mini-cheat-sheet de atajos en `static/guide.html`.

### Etapa 2 — Indicadores en vivo (items 2, 3 y 8)

**Qué:** punto ● de "modificado", posición `Ln/Col` bajo el editor, chip de
estado + `z` en el encabezado del resultado.

Diseño:

- **● modificado:** chip junto al input de nombre + sufijo `•` en
  `document.title`. Fuente de verdad: `isDirty()` existente. Para eliminar el
  riesgo de drift (invariante 2): crear `setSavedContent(v)` que asigna y
  refresca el indicador, y reemplazar los 5 sitios crudos por el helper.
  Refrescar también desde `refreshEditor` (ya cuelga de `input`).
- **Ln/Col:** barra propia al pie de `.editor-wrap` (alineada a la derecha,
  tipografía de `--muted`). **No** usar `#status-line` (invariante 3).
  Eventos: `keyup`, `click`, `input`, `select` sobre `#editor`
  (`selectionchange` no es confiable en textareas en todos los navegadores).
  Col en unidades de `selectionStart` (UTF-16) — documentado, sin sorpresas.
- **Chip del run:** `#run-header` pasa de `textContent` a DOM estructurado
  (span de texto + chip). Datos: llamar `parseSolution` **siempre** en
  `render()` (hoy solo se llama en la pestaña Tablas) y compartir el
  resultado. Hoy `run-header` se asigna con `textContent` (l. 1578) — ojo:
  reasignar `textContent` después de insertar el chip lo borra.

Mapeo de estados del chip (fuente: `info.Status` del imprimible de glpsol):

| Estado | Color | Texto |
|---|---|---|
| `OPTIMAL`, `INTEGER OPTIMAL` | ok | `z = <valor> · ÓPTIMO` |
| `FEASIBLE`, `INTEGER NON-OPTIMAL` | acento | `z = <valor> · FACTIBLE` |
| `INFEASIBLE`, `UNBOUNDED`, `UNDEFINED` | err | sin `z` |
| Sin parseo + `exit_code = 0` | neutro | `exit 0` |
| Sin parseo + `exit_code ≠ 0` | err | `exit N` (el detalle ya vive en `#status-line`) |
| `killed_by_timeout` | err | `⏱ cortado` |
| Run restaurado (`exit_code: None`) | neutro | `⟲ guardado` |

`z` se extrae de `info.Objective` (`"z = 17 (MINimum)"`) con
`/^(\S+)\s*=\s*([-+]?[\d.]+(?:[eE][-+]?\d+)?)/`. Si trae el nombre del
objetivo, mostrarlo en el `title` del chip.

Verificación:

- [ ] El ● aparece al primer carácter editado, desaparece al guardar
      (botón, `Ctrl+S` y el autosave de Resolver) y al abrir/crear modelo.
- [ ] `Ln/Col` correcto tras pegar multilínea, click con mouse y flechas.
- [ ] El chip coincide con el estado del log para un modelo óptimo, uno
      infactible y un timeout; un run restaurado muestra `⟲ guardado`.

### Etapa 3 — Zoom de fuente (item 5) — DESCARTADA

> Descartada por decisión del usuario (2026-09-06) antes de implementarse:
> es la etapa con más trabajo acoplado al textarea que una futura migración
> a CodeMirror daría gratis. Se conserva documentada como referencia.

**Qué:** `Ctrl+rueda` sobre el editor cambia el tamaño de fuente; `Ctrl+0`
restaura.

Diseño:

- Variables CSS en `:root`: `--code-fs: 13px` (default) y `--code-lh: 20px`.
  Refactor de los TRES consumidores del invariante 1: `#gutter` (font
  12px pero `line-height: var(--code-lh)`), `#highlight`/`#editor`
  (`font-size: var(--code-fs); line-height: var(--code-lh)`), `#gutter .ln`
  (`height: var(--code-lh)`).
- El scroll a línea con error (l. 1551) pasa a leer el line-height real:
  cachear `lineHeightPx()` (un `getComputedStyle` por cambio de zoom, no por
  evento).
- Wheel en `.editor-wrap` con `{ passive: false }` + `preventDefault` **solo
  si `ctrlKey`** (sin esto el navegador hace zoom de página y el listener no
  puede cancelarlo — gotcha clásico). Paso ±1px, rango 10–24. `Ctrl+0` → 13.
- Persistir en `localStorage['mctd.codeZoom']`; aplicar al cargar, antes del
  primer `refreshEditor()`.

Tradeoffs:

| Fork | Decisión | Razón |
|---|---|---|
| Hijack de `Ctrl+rueda` (es el zoom del navegador) | Sí, solo sobre el editor | Mismo comportamiento que VSCode; fuera del editor el zoom de página sigue |
| Zoom también al panel de resultados | No (queda como follow-up trivial reusando la var) | Mantener el blast radius chico |
| Config en `config.json` vs `localStorage` | `localStorage` | Ver §2: preferencia de vista, no operación |

Verificación:

- [ ] A 10px y a 24px el gutter sigue alineado con el texto línea a línea,
      scroll sincronizado y resaltado sin desfase.
- [ ] Con error de sintaxis, el scroll automático aterriza en la línea
      correcta a cualquier zoom.
- [ ] El zoom sobrevive recargar la página; `Ctrl+0` restaura.

### Etapa 4 — Copiar resultados (item 6)

**Qué:** botón `⧉ Copiar` en la barra del panel de resultados.

Diseño:

- Pestaña Tablas: recorrer `#result-body table tr` → celdas
  `textContent.join('\t')`, filas join `\n` → TSV pegable en Excel/Sheets.
- Pestañas de texto (Solución/Log/Sensibilidad): copiar
  `#result-body.textContent`.
- Deshabilitado/oculto si no hay run o el cuerpo dice "(vacío)".
- Helper `copyText(s)`: `navigator.clipboard.writeText` con **fallback** a
  `execCommand('copy')` sobre un `<textarea>` oculto temporal (invariante 4).
  Feedback inmediato: el label del botón cambia a "Copiado ✓" 1.5s —
  independiente de los toasts (E5 puede mejorarlo después, sin acoplarse).

Borde conocido: nombres de variables/columnas con tabuladores internos son
prácticamente imposibles en el imprimible de glpsol (columnas alineadas por
espacios); el join simple es suficiente — documentado, no escapar.

Verificación:

- [ ] Pegar en una planilla separa por columnas; coincide con la tabla visible.
- [ ] Desde `http://<ip-lan>` (sin secure context) el botón sigue
      funcionando vía fallback.

### Etapa 5 — Filtro de archivos y toasts (items 9 y 10)

**Qué:** input de filtro sobre el árbol de modelos; notificaciones no
bloqueantes que reemplazan los `alert()`.

Filtro:

- Input compacto entre el encabezado "Archivos" y el árbol, con placeholder
  "Buscar…", `Esc` limpia. Matching: substring case-insensitive con
  `toLowerCase()` (sin folding de acentos — invariante 7).
- Semántica de carpetas: se muestra una carpeta si algún descendiente matchea;
  mientras hay filtro activo las carpetas matcheadas se expanden forzadamente
  y al limpiar se **restaura** el estado de colapso previo (snapshot del Set
  en memoria al activar el filtro).
- Implementación sugerida: filtrar `treeData` antes de renderizar (mismo
  `renderTreeLevel`), conservando la data completa para restaurar.
- Limitación aceptada y documentada: con filtro activo, el drag & drop solo
  tiene como destinos las carpetas visibles.

Toasts:

- Contenedor fixed abajo-derecha, `z-index: 70` (por encima de tooltips 60 y
  overlays 25). Variantes `ok`/`err`, auto-dismiss 4s, click cierra, máximo 4
  apilados (el más viejo se elimina primero).
- Reemplaza los `alert()` existentes: `saveCurrentModel` (2), handler de
  Resolver (3), `exportModelFile` (1), `importZipFile` (3). Todos son
  notificaciones terminales — ningún flujo depende del bloqueo del `alert`
  (verificado: cada llamada es seguida de `return`).
- Extra barato: toast `Guardado` silencioso al guardar con éxito (hoy el
  único feedback es el refresh del sidebar).

Verificación:

- [ ] Filtrar "tp1" muestra `tp1/…` y sus carpetas ancestro expandidas;
      limpiar restaura el colapso exacto previo.
- [ ] Guardar con error de red notifica sin bloquear y sin quedar pegado.
- [ ] Los toasts no quedan debajo de ningún modal ni tooltip.

### Etapa 6 — Estado persistido por ejecución (item 7)

**Qué:** punto de color por run en el historial del sidebar. Es la única etapa
que toca el backend: el exit code hoy no se persiste (verificado, §1 item 7).

Diseño (sidecar file):

- Al finalizar cada run, escribir `results/<árbol>/<modelo>-{stamp}.exit`
  con una línea: el código de salida, o `timeout`.
- `run_files_for` (app.py l. 287): extender la alternancia a
  `(txt|log|sol|rng|exit)` — **la poda del historial circular lo borra gratis**
  porque ya itera `runs[stamp].values()` (l. 373).
- `RUN_FILE_RE` (l. 47): misma extensión. `all_runs` queda igual: su `seen`
  se clavea por `(model, stamp)`, un archivo extra por run es inofensivo.
- `GET /api/runs/{name}`: agregar `"exit"` por item (leer el sidecar si
  existe; ausente = `null`).
- Frontend: punto en cada `.run-item` — verde (`0`), rojo (otro código /
  `timeout`), gris (sin sidecar: runs previos a esta etapa). Tooltip con el
  detalle.
- Export/import zip: el manifest ya categoriza runs; el `.exit` debe viajar
  como artefacto de run. Los zips viejos sin `.exit` importan normal (punto
  gris) — sin migración, misma filosofía de req_3.

Tradeoffs:

| Fork | Veredicto | Razón |
|---|---|---|
| **A. Sidecar `.exit`** | **Elegida** | El filesystem ya es la fuente de verdad; poda y export lo absorben casi gratis; runs viejos simplemente no lo tienen |
| B. Inferir estado parseando `.txt`/`.log` al listar | Rechazada | Lee N archivos por listado; frágil (¿qué cuenta como fallo en salida parcial?) |
| C. Estado en `.meta/<modelo>.json` | Rechazada | La meta es por modelo (fecha de creación); mezclar estado por run agrega un segundo escritor y complica la poda |

Verificación:

- [ ] Run exitoso, run con error de sintaxis y timeout: tres puntos distintos
      que sobreviven recargar la página (listado restaurado desde disco).
- [ ] La poda elimina también el `.exit` (no quedan huérfanos).
- [ ] Zip exportado con `.exit` e importado conserva los colores.

## 5. Orden y despliegue

1. Las etapas 1–5 son `static/` puro: `docker compose up -d --build` y listo
   (invariante 5). Cualquiera puede quedar sola en main sin romper nada.
2. La etapa 6 agrega `app.py`: mismo comando (rebuild completo), desplegable
   independientemente de las demás — el frontend de etapa 6 es tolerante a
   `exit: null`.
3. Cada etapa = un work unit con su verificación manual (checklist de la
   etapa) antes de pasar a la siguiente.

## 6. Fuera de alcance (explícito)

- Multi-cursor y cualquier mejora que requiera CodeMirror/Monaco (req futuro).
- Buscar/reemplazar dentro del editor; autocompletado; bracket matching.
- Tema claro/oscuro; zoom del panel de resultados.
- Atajos configurables por el usuario.
- Deshacer/rehacer custom (se mantiene el undo nativo del textarea).
