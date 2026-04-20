# Analisis para nuevo proyecto POSITIVA

## 1. Revision de Pure-IA

`Pure-IA` ya resuelve una decision importante de arquitectura: no mezclar silenciosamente la ruta digital con la ruta escaneada.

Lo que si conviene reutilizar:

- Deteccion previa de texto util para decidir `digital` o `scan`
- Extraccion local, sin dependencia de servicios externos
- `pypdf` para digital con fallback de `pypdfium2`
- `pypdfium2` + `pytesseract` para OCR local

Lo que no conviene copiar tal cual:

- La arquitectura por perfiles de poliza de `Pure-IA` esta pensada para otros documentos y otros campos.
- POSITIVA necesita un parser orientado a boletas de liquidacion, con foco en lineas contables y totales.
- Para esta fase no hace falta una API grande ni una cola de workers.

## 2. Tecnologias recomendadas para POSITIVA

- `Python`: velocidad de implementacion y ecosistema PDF/OCR maduro
- `pypdf`: texto digital rapido
- `pypdfium2`: fallback de texto y render de paginas para OCR
- `pytesseract`: OCR local y barato para el caso scan
- `pandas` + `openpyxl`: exportacion Excel multihoja
- `decimal.Decimal`: manejo financiero exacto en validaciones

## 3. Criterio de extraccion

La extraccion POSITIVA no debe depender de posiciones fijas del PDF.

La estrategia correcta es:

- reconocer lineas de encabezado
- identificar oficinas por nombre
- parsear cada linea de detalle usando anclas estables:
  - fecha `YYYY-MM-DD`
  - cuatro columnas numericas al final
  - poliza y documento antes de la fecha

Esto la hace mucho mas robusta ante pequenos desplazamientos visuales del PDF.

## 4. Que haria antes de construir la version completa

- Recolectar al menos 15 a 30 PDFs POSITIVA por cada tipo real:
  - digital limpio
  - digital con tablas partidas
  - escaneado limpio
  - escaneado con ruido
- Confirmar el layout real de negocio:
  - columnas obligatorias
  - significado exacto de `Total`, `Total Neto`, `IGV` y `Dscto`
  - si existen notas credito, reversos y descuentos negativos
- Acordar el Excel final de negocio:
  - columnas requeridas
  - nombres de hojas
  - reglas de redondeo
  - validaciones visibles
- Construir una matriz de pruebas por aseguradora y por formato antes de escalar a otras companias.

## 5. Entregable de esta fase

En este proyecto se deja una primera base local para POSITIVA con:

- deteccion `digital` vs `scan`
- parser especifico de boletas POSITIVA
- exportacion a Excel
- hoja de validaciones para contrastar sumas por oficina y por documento
