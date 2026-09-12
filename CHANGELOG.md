# Changelog

## 0.7.1 — 2026-09-11

- Panel de resultados ampliable hasta el 85% del ancho de la ventana (antes tope fijo de 720px).
- Doble clic en el divisor alterna entre máximo (85%) y mínimo (280px) en lugar de resetear a 420px.

## 0.7.0 — 2026-09-11

- Atajos de teclado (`docs/req_4.md`): `Ctrl+S` guardar, `Ctrl+Enter` resolver, `Ctrl+/` comentar/descomentar (con undo nativo), `Esc` cierra modales.
- Indicadores en vivo: punto ● de cambios sin guardar (toolbar + título), posición del cursor Ln/Col al pie del editor, chip de estado de la corrida (ÓPTIMO/FACTIBLE/INFACTIBLE/cortado) en el header de resultados.
- Baseline único del dirty flag: todo save/open/clear pasa por `setSavedContent`, el punto nunca queda desincronizado.

## 0.6.0 — 2026-09-06

- Exportar: todo o una carpeta como `.zip` (modelos + meta + runs + manifest); modelo individual como `.mod` descargable.
- Importar: botón Abrir o drag & drop de `.zip` con dry-run, confirmación y sobrescritura opcional; `.mod` se abre directo en el editor.
- Defensa zip-slip: toda ruta se re-valida y reconstruye; runs nunca se sobrescriben.

## 0.5.1 — 2026-09-06

- Guard de cambios sin guardar al abrir otro modelo o crear uno nuevo: guardar / no guardar / cancelar.
- Aviso al cerrar la pestaña con cambios sin guardar; el autosave de Resolver ahora marca el estado como guardado.

## 0.5.0 — 2026-09-05

- Carpetas anidadas (hasta 5 niveles) con árbol colapsable en el sidebar (`docs/req_3.md`).
- Drag & drop interno: mover modelos a carpetas; menú ⋮ con "Mover a…".
- Menú ⋮ de carpeta: renombrar, eliminar (recursivo, con confirmación), nueva subcarpeta.
- `results/` y `.meta/` espejan el árbol; el historial sigue al modelo al mover.
- Modelos y ejecuciones existentes (planos) siguen funcionando sin migración.

## 0.4.0 — 2026-09-05

- Drag & drop de archivos al sidebar para importar modelos.
- Menú por archivo (⋮): renombrar (modal validado) y eliminar.
- Renombrado mueve modelo + meta + ejecuciones juntas (`POST /api/models/{name}/rename`).
- Layout de altura fija: cada panel scrollea internamente, la página no se estira.
- Timestamps en hora Argentina (GMT-3); el contenedor corría en UTC.

## 0.3.1 — 2026-09-03

- Timeout duro graceful: devuelve log/solución parcial (`killed_by_timeout`) en vez de un 504 vacío.
- Corridas serializadas: una glpsol a la vez; reintento en curso recibe `409`.

## 0.3.0 — 2026-08-30

- Sistema de configuración global (botón ⚙) con tooltip explicativo en cada opción: solver (método, primal/dual, presolve, chequeo exacto, semilla, límites de tiempo/memoria), enteros (relajación LP, gap, cortes), salida (análisis de sensibilidad, solución plana, modo de vista), ejecución (límites blando/duro, historial a guardar) y plantilla del editor. Persiste en `config/config.json`, valida en backend y aplica sin reiniciar (`docs/req_1.md`).
- Ejecuciones del modelo seleccionado en el sidebar (nuevo endpoint `GET /api/runs/{name}`), con mensaje de selección cuando no hay archivo abierto y altura fija de la sección para evitar saltos de layout.
- Pestaña Tablas: análisis de sensibilidad formateado (restricciones y variables, rangos de actividad y de coeficiente objetivo como intervalos mín–máx) y cotas infinitas explícitas (`-∞` / `+∞`).
- Cada ejecución persiste además solución plana (`.sol`) y reporte de sensibilidad (`.rng`) según configuración; el comando glpsol efectivo se ve al pasar el cursor por el encabezado del resultado.
- Volumen Docker para `config/` y guía de uso actualizada.

## 0.2.0 — 2026-08-26

- Editor con numeración de líneas (gutter lateral).
- Resaltado de sintaxis MathProg: palabras reservadas, comentarios (`/* */` multilínea y `#`), cadenas y números.
- Marcado de la línea con error de sintaxis reportada por glpsol (gutter rojo + mensaje en la barra de estado).

## 0.1.0 — 2026-08-25

Primera versión funcional.

- Editor web de modelos MathProg (.mod) con guardado y ejecución vía GLPK (glpsol).
- Panel de solución/log con última ejecución restaurada al abrir un modelo.
- Sidebar: últimas 10 ejecuciones (clic para ver) + archivos ordenados por fecha de creación.
- Historial circular de 5 ejecuciones por modelo.
- Borrado de modelos con confirmación (incluye sus ejecuciones).
- Guía de uso (`/guide`) con logo y favicon.
- Entorno Docker con volúmenes para `models/` y `results/`.
