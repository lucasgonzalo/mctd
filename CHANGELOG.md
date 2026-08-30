# Changelog

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
