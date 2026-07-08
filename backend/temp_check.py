import sys
from pdf_service import PdfService
from pathlib import Path

pdf = Path('../samples/Igenylesi_dokumentumok_elso_6_oldal.pdf')
if pdf.exists():
    fields = PdfService().extract_acroform_fields(pdf)
    for f in fields:
        name = f['pdf_field_name']
        if 'Igenylesi' in name or 'lcs' in name or 'Kölcsön' in name:
            print(f"{name}: y={f['rect']['y']}")
