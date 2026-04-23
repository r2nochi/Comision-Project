from pathlib import Path
from commission_system.profiles import rotatable_liquidation_layout as mod
import pypdfium2 as pdfium
import pytesseract
from pytesseract import Output
from commission_system.ocr import preprocess_image, ensure_tesseract

path = Path(r'output\rotated_tests\PROTECTA LIQ-000088731 (06.NOV.25)__upright_test.pdf')
rows, warnings = mod._extract_rows_for_rotation(file_path=path, rotation=0, insurer='PROTECTA')
print('rows', len(rows), 'warnings', len(warnings))
for w in warnings[:20]:
    print('warning', w)
print('sample rows')
for row in rows[:8]:
    print(row)

ensure_tesseract()
pdf = pdfium.PdfDocument(str(path))
page = pdf.get_page(0)
bitmap = page.render(scale=mod.RENDER_SCALE)
image = bitmap.to_pil().copy()
processed = preprocess_image(image)
data = pytesseract.image_to_data(processed, lang='spa', config='--psm 11', output_type=Output.DICT)
processed.close(); image.close(); bitmap.close(); page.close(); pdf.close()

tokens = mod._collect_tokens(data)
clusters = mod._group_tokens_into_lines(tokens)
for i, cluster in enumerate(clusters[:160], 1):
    text = mod._cluster_text(cluster)
    lefts = [left for _, left, _ in cluster]
    tops = [top for top, _, _ in cluster]
    print(f'{i:03d} top={min(tops):04d} left={min(lefts):04d}-{max(lefts):04d} :: {text}')
