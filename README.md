# Comision Project - Multiaseguradora

Extractor local para liquidaciones y boletas de comisiones de varias aseguradoras, con deteccion por contenido del PDF.

## Objetivo

- Detectar si el PDF es digital o escaneado.
- Detectar la aseguradora por el contenido del PDF, no por el nombre del archivo.
- Elegir automaticamente el perfil/layout de extraccion.
- Exportar la informacion a un Excel estructurado.

## Enfoque tecnico

- PDF digital: `pypdf` + fallback por pagina con `pypdfium2`
- PDF escaneado: render con `pypdfium2` + OCR local con `pytesseract`
- Parseo por perfiles/layouts y no por coordenadas fijas
- Excel: `pandas` + `openpyxl`
- Web local: `FastAPI` + `uvicorn`

## Instalacion

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Ejecucion

Procesamiento por lote:

```powershell
python .\run_commissions.py --input-dir .\files --output .\output\comisiones_multiaseguradora.xlsx
```

Web local:

```powershell
python .\run_web.py
```

Luego abre:

```text
http://127.0.0.1:8000
```

Tunnel Cloudflare:

```powershell
.\start_cloudflare_tunnel.ps1
```

## Salida

El Excel genera estas hojas:

- `resumen_documentos`
- `detalle_comisiones`
- `totales_reportados`
- `validaciones`

## Notas

- El sistema primero usa texto digital y luego OCR local cuando hace falta.
- Para algunos layouts como RIMAC se puede preferir OCR completo aunque el PDF sea digital, porque el logo y la tabla quedan mejor reconstruidos.
- Los archivos Excel generados por la web tambien se guardan en `output` con fecha, hora y nombre del PDF.
