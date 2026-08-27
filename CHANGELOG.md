# Changelog

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
