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

Jobs por manifest:

```powershell
python .\run_jobs.py build-manifest --input-dir .\files --manifest-path .\output\jobs\manifests\all_pdfs_auto.manifest.json --output-root .\output\jobs\results --expected-insurer AUTO --include-scans
python .\run_jobs.py run-manifest --manifest .\output\jobs\manifests\all_pdfs_auto.manifest.json
```

Compatibilidad con el flujo anterior `job + queue`:

```powershell
python .\run_jobs.py build-manifests --input-dir .\files --manifests-dir .\output\jobs\manifests --queue-path .\output\jobs\queues\all_pdfs_auto.queue.json --output-root .\output\jobs\results --expected-insurer AUTO --include-scans
python .\run_jobs.py run-job --manifest .\output\jobs\manifests\001__20101097448_LIQ_COMISION_SECREX_NRO_16792.job.json
python .\run_jobs.py run-queue --queue .\output\jobs\queues\all_pdfs_auto.queue.json
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
