"""Digit-box sor-csoportosító eszköz mapping-ekhez.

A banki nyomtatványok összeg/dátum rovatai jellemzően 1 karakteres AcroForm
dobozok sorai. Ez az eszköz GEOMETRIA alapján azonosítja egy oldal egy
y-sávjában az összetartozó dobozokat (x-rendezve), hogy azokból a mapping
``character_groups`` bejegyzései készülhessenek — kézi számbavétel helyett.

CLI:
    python -m src.mapping.digit_row_grouper <pdf> <page_number> [--y0 Y] [--y1 Y] [--x0 X] [--x1 X]

Visszatérés (JSON stdout): a talált dobozok x-sorrendben, becsült
sor-címkével együtt. A canonical-hozzárendelés az ember döntése marad —
az eszköz a mechanikus részét automatizálja.
"""

from __future__ import annotations

import argparse
import json
import sys


def find_digit_row(
    pdf_path: str,
    page_number: int,
    y0: float | None = None,
    y1: float | None = None,
    x0: float = 0.0,
    x1: float = 10_000.0,
    min_width: float = 8.0,
    max_width: float = 20.0,
    y_tolerance: float = 12.0,
) -> dict:
    """Egy oldal adott sávjában lévő digit-dobozok x-rendezett listája.

    A ``y0``/``y1`` elhagyható — ilyenkor az összes jelölt sor kilistázódik
    (csoportosítva y-koordináta szerint), és a hívó választja ki a sort.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_number - 1]
        words = page.get_text("words")
        rows: dict[int, list[tuple[float, str]]] = {}
        for w in page.widgets():
            if not w.field_name:
                continue
            r = w.rect
            if not (min_width < r.width < max_width):
                continue
            if not (x0 <= r.x0 and r.x1 <= x1):
                continue
            if y0 is not None and r.y0 < y0 - y_tolerance:
                continue
            if y1 is not None and r.y0 > y1 + y_tolerance:
                continue
            rows.setdefault(round(r.y0), []).append((r.x0, w.field_name))

        out_rows = []
        for y in sorted(rows):
            members = [name for _, name in sorted(rows[y])]
            # Becsült sor-címke: a címkeoszlop (a sor bal szélétől balra)
            # szövege a sor környékén.
            min_x = min(x for x, _ in rows[y])
            label = " ".join(
                t for wx0, wy0, wx1, wy1, t, *_ in words
                if wx1 < min_x - 2 and y - 40 <= wy0 <= y + 8
            )
            out_rows.append({
                "y": y,
                "member_fields": members,
                "box_count": len(members),
                "label_guess": label[-100:],
            })
        return {"page": page_number, "rows": out_rows}
    finally:
        doc.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Digit-box sorok keresése PDF-ben")
    ap.add_argument("pdf")
    ap.add_argument("page", type=int)
    ap.add_argument("--y0", type=float, default=None)
    ap.add_argument("--y1", type=float, default=None)
    ap.add_argument("--x0", type=float, default=0.0)
    ap.add_argument("--x1", type=float, default=10_000.0)
    args = ap.parse_args(argv)
    result = find_digit_row(args.pdf, args.page, args.y0, args.y1, args.x0, args.x1)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
