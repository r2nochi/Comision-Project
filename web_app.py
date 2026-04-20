from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from commission_system.excel_exporter import export_results
from commission_system.pipeline import process_file
from commission_system.profiles.registry import SUPPORTED_INSURERS
from commission_system.utils import sanitize_output_stem


APP_TITLE = "Comision Project"
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
UPLOAD_DIR = OUTPUT_DIR / "uploads"

app = FastAPI(title=APP_TITLE)


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse(_render_home())


@app.get("/health", response_class=HTMLResponse)
def health() -> HTMLResponse:
    return HTMLResponse("ok")


@app.get("/download/{filename}")
def download(filename: str) -> FileResponse:
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )


@app.post("/extract", response_class=HTMLResponse)
async def extract(
    pdf_file: UploadFile = File(...),
    expected_insurer: str = Form("AUTO"),
) -> HTMLResponse:
    if not pdf_file.filename or not pdf_file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Debes subir un archivo PDF.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    original_stem = sanitize_output_stem(Path(pdf_file.filename).stem)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    uploaded_pdf = UPLOAD_DIR / f"{timestamp}__{original_stem}.pdf"
    uploaded_pdf.write_bytes(await pdf_file.read())

    document = process_file(uploaded_pdf, expected_insurer=expected_insurer)
    excel_name = f"{timestamp}__{original_stem}__{sanitize_output_stem(document.detected_insurer)}.xlsx"
    excel_path = OUTPUT_DIR / excel_name
    export_results([document], excel_path)

    return HTMLResponse(_render_result(document, excel_name))


def _render_home() -> str:
    insurer_options = "\n".join(
        f'<option value="{insurer}">{insurer}</option>' for insurer in ["AUTO", *SUPPORTED_INSURERS]
    )
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{APP_TITLE}</title>
  <style>
    :root {{
      --bg: #f4efe7;
      --card: #fffaf2;
      --ink: #1f2a2e;
      --accent: #0f7f67;
      --accent-2: #d98f2b;
      --muted: #6d756f;
      --border: #d9ccb5;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(217, 143, 43, 0.18), transparent 35%),
        radial-gradient(circle at bottom right, rgba(15, 127, 103, 0.18), transparent 30%),
        var(--bg);
      min-height: 100vh;
    }}
    .wrap {{
      max-width: 920px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(15,127,103,.12), rgba(217,143,43,.12));
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 28px;
      box-shadow: 0 14px 30px rgba(43, 39, 34, .08);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 32px;
      line-height: 1.05;
    }}
    p {{
      color: var(--muted);
      line-height: 1.55;
    }}
    form {{
      display: grid;
      gap: 16px;
      margin-top: 24px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 22px;
    }}
    label {{
      font-weight: 600;
      display: grid;
      gap: 8px;
    }}
    input, select, button {{
      font: inherit;
    }}
    input, select {{
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: #fff;
    }}
    button {{
      border: 0;
      border-radius: 999px;
      padding: 14px 20px;
      background: linear-gradient(90deg, var(--accent), #15937a);
      color: #fff;
      font-weight: 700;
      cursor: pointer;
    }}
    .note {{
      margin-top: 18px;
      padding: 16px;
      border-radius: 16px;
      background: rgba(255,255,255,.65);
      border: 1px dashed var(--border);
    }}
    .tags {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 14px;
    }}
    .tag {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 13px;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <h1>Extracción de comisiones por contenido real del PDF</h1>
      <p>
        Sube un PDF y el backend detectará la aseguradora por el texto o logo del documento,
        aunque el nombre del archivo esté mal. Luego construirá el Excel y lo dejará guardado
        también en la carpeta <code>output</code>.
      </p>
      <form action="/extract" method="post" enctype="multipart/form-data">
        <label>
          PDF de comisión
          <input type="file" name="pdf_file" accept=".pdf,application/pdf" required />
        </label>
        <label>
          Aseguradora esperada
          <select name="expected_insurer">
            {insurer_options}
          </select>
        </label>
        <button type="submit">Procesar y generar Excel</button>
      </form>
      <div class="note">
        La descarga usa el navegador, así que el archivo irá a la carpeta de descargas predeterminada del dispositivo que abrió la web.
      </div>
      <div class="tags">
        {"".join(f'<span class="tag">{insurer}</span>' for insurer in SUPPORTED_INSURERS)}
      </div>
    </section>
  </main>
</body>
</html>"""


def _render_result(document, excel_name: str) -> str:
    warning_html = "".join(f"<li>{warning}</li>" for warning in document.warnings) or "<li>Sin observaciones.</li>"
    marker_html = "".join(f"<span class='chip'>{marker}</span>" for marker in document.detection_markers)
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Resultado - {APP_TITLE}</title>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: #f6f3ee;
      color: #1e272a;
    }}
    .wrap {{
      max-width: 900px;
      margin: 0 auto;
      padding: 28px 20px 48px;
    }}
    .card {{
      background: white;
      border-radius: 24px;
      padding: 24px;
      box-shadow: 0 16px 40px rgba(0,0,0,.08);
      border: 1px solid #e2d8ca;
    }}
    h1 {{ margin-top: 0; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin: 20px 0;
    }}
    .item {{
      background: #fbf8f2;
      border: 1px solid #ece1d2;
      border-radius: 16px;
      padding: 14px;
    }}
    .label {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .06em;
      color: #6c746e;
    }}
    .value {{
      margin-top: 6px;
      font-weight: 700;
    }}
    .actions {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 22px;
    }}
    a.button {{
      display: inline-block;
      background: #0f7f67;
      color: white;
      padding: 14px 18px;
      border-radius: 999px;
      text-decoration: none;
      font-weight: 700;
    }}
    .secondary {{
      background: #d98f2b !important;
    }}
    ul {{
      line-height: 1.55;
      color: #4d5659;
    }}
    .chips {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 10px;
    }}
    .chip {{
      padding: 7px 10px;
      border-radius: 999px;
      background: #eff7f4;
      color: #0f7f67;
      font-size: 12px;
      border: 1px solid #c7e3db;
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="card">
      <h1>Excel generado</h1>
      <p>La detección se hizo usando el contenido real del PDF. Si tu navegador lo permite, la descarga arrancará desde el botón de abajo.</p>
      <div class="grid">
        <div class="item"><div class="label">Aseguradora detectada</div><div class="value">{document.detected_insurer}</div></div>
        <div class="item"><div class="label">Perfil detectado</div><div class="value">{document.detected_profile}</div></div>
        <div class="item"><div class="label">Modo de entrada</div><div class="value">{document.input_mode}</div></div>
        <div class="item"><div class="label">Documento</div><div class="value">{document.document_number or "N/D"}</div></div>
        <div class="item"><div class="label">Filas de detalle</div><div class="value">{len(document.detail_rows)}</div></div>
        <div class="item"><div class="label">Puntaje detección</div><div class="value">{document.detection_score}</div></div>
      </div>
      <div class="chips">{marker_html}</div>
      <h2>Observaciones</h2>
      <ul>{warning_html}</ul>
      <div class="actions">
        <a class="button" href="/download/{excel_name}" download>Descargar Excel</a>
        <a class="button secondary" href="/">Procesar otro PDF</a>
      </div>
    </section>
  </main>
</body>
</html>"""
