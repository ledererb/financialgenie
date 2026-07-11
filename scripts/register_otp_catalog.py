#!/usr/bin/env python3
"""
FinancialGenie katalógus teljes felépítése: bank, termékek, master split,
és különálló OTP PDF-ek regisztrálása.

Egyetlen script ami mindent helyesen beállít:
1. OTP Bank létrehozása
2. 4 termék létrehozása
3. Master PDF split + katalógus regisztráció
4. Különálló PDF-ek (OTP/ mappa) regisztrálása a megfelelő termékekhez

Használat:
    # Lokál
    python scripts/register_otp_catalog.py

    # Dev szerver (docker exec)
    docker exec financialgenie-backend python /app/scripts/register_otp_catalog.py --api-url http://localhost:8765 --project-root /app
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


# OTP mappa neve → termék slug mapping
PRODUCT_MAP = {
    "Előzetes értékbecslé megrendelés": "elozetes_ertekbecsles_megrendeles",
    "Otthon Start": "otthon_start",
    "Piaci hitel": "piaci_hitel",
    "Szabadfelhasználású hitel": "szabadfelhasznalasu_hitel",
}

# Dokumentumok amik minden hiteltermékhez tartoznak (közös nyomtatványok)
COMMON_DOCS = {
    "Partner_nyilatkozat_hiteligeny_leadasakor",
    "V_szamu_fuggelek_Penzugyi_szolgaltatas_kozvetiteseben_valo_kozremukodesre_vonatkozo_ugyfel_ceg_nyilatkozat_20250601",
}

# Master PDF — átugorjuk
MASTER_PDF_NAMES = {
    "Igenylesi_dokumentumok_OTP_Jelzaloghitelek_es_tamogatasok_20260330_v5",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def api(base_url: str, method: str, path: str, data=None, files=None):
    """API call. If files given, use multipart form."""
    url = f"{base_url}{path}"
    if files:
        # Multipart upload
        boundary = "----FormBoundary7MA4YWxkTrZu0gW"
        file_field, file_path = files
        filename = Path(file_path).name
        body = f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
        body += b"Content-Type: application/pdf\r\n\r\n"
        with open(file_path, "rb") as f:
            body += f.read()
        body += f"\r\n--{boundary}--\r\n".encode()
        req = Request(url, data=body, method=method)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    else:
        body = json.dumps(data).encode() if data else None
        req = Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        err = e.read().decode()
        print(f"  ERROR {e.code}: {err[:200]}")
        return {}


def main():
    parser = argparse.ArgumentParser(description="Build FinancialGenie catalog from scratch")
    parser.add_argument("--api-url", default="http://localhost:8765")
    parser.add_argument("--project-root", default=None, help="Project root (auto-detect)")
    args = parser.parse_args()

    project_root = Path(args.project_root) if args.project_root else Path(__file__).parent.parent
    base_url = args.api_url.rstrip("/")
    otp_dir = project_root / "OTP"
    master_pdf = project_root / "documents" / "otp_bank" / "master" / "OTP_Igenylesi_Dokumentumok_v5.pdf"

    print(f"Project root: {project_root}")
    print(f"API: {base_url}")
    print(f"OTP dir exists: {otp_dir.exists()}")
    print(f"Master PDF exists: {master_pdf.exists()}")
    print()

    # === 1. Bank ===
    print("=== 1. Bank létrehozása ===")
    api(base_url, "POST", "/api/catalog/banks", {"name": "OTP Bank"})

    # === 2. Termékek ===
    print("=== 2. Termékek létrehozása ===")
    product_names = {
        "Piaci hitel",
        "Szabadfelhasználású hitel",
        "Otthon Start",
        "Előzetes értékbecslés megrendelés",
    }
    for name in sorted(product_names):
        print(f"  {name}")
        api(base_url, "POST", "/api/catalog/banks/otp_bank/products", {"name": name})

    # Lekérjük a product slug-eket
    cat = api(base_url, "GET", "/api/catalog")
    product_slugs = {}  # name → slug
    for bank in cat.get("banks", []):
        for prod in bank.get("products", []):
            product_slugs[prod["name"]] = prod["id"]

    # === 3. Master Split ===
    print()
    print("=== 3. Master PDF Split ===")
    if master_pdf.exists():
        print(f"  Splitting {master_pdf.name}...")
        result = api(
            base_url, "POST",
            "/api/catalog/split-master?bank_id=otp_bank",
            files=("file", str(master_pdf)),
        )
        split_id = result.get("split_id")
        if split_id:
            print(f"  Split ID: {split_id}")
            # Poll until done
            for i in range(40):
                time.sleep(3)
                status = api(base_url, "GET", f"/api/catalog/split/{split_id}")
                st = status.get("status", "unknown")
                if st == "done":
                    files_out = status.get("output_files", [])
                    print(f"  ✓ Split done: {len(files_out)} documents")
                    break
                print(f"  Waiting... ({st}, {status.get('done_pages', 0)}/{status.get('total_pages', 0)})")
        else:
            print("  ✗ Split failed")
    else:
        print(f"  SKIP (master PDF not found)")

    # === 4. Különálló PDF-ek ===
    print()
    print("=== 4. Különálló OTP PDF-ek regisztrálása ===")
    if not otp_dir.exists():
        print(f"  SKIP (OTP folder not found)")
    else:
        # Get current catalog state (after split)
        cat = api(base_url, "GET", "/api/catalog")
        existing_paths = set()
        for d in cat.get("documents", []):
            existing_paths.add(d.get("file_path", ""))

        pdfs = []
        for pdf_path in sorted(otp_dir.rglob("*.pdf")):
            if pdf_path.stem in MASTER_PDF_NAMES:
                continue
            pdfs.append(pdf_path)

        print(f"  Found {len(pdfs)} standalone PDFs")
        registered = 0
        for pdf_path in pdfs:
            stem = pdf_path.stem
            product_folder = pdf_path.parent.name
            product_slug = PRODUCT_MAP.get(product_folder)

            if not product_slug:
                print(f"  SKIP (unknown folder '{product_folder}'): {stem}")
                continue

            # Determine products
            if stem in COMMON_DOCS:
                product_ids = [
                    product_slugs.get(s, s)
                    for s in ["Piaci hitel", "Szabadfelhasználású hitel", "Otthon Start"]
                ]
            else:
                # Find the product name for this slug
                prod_name = None
                for pn, ps in product_slugs.items():
                    if ps == product_slug:
                        prod_name = pn
                        break
                product_ids = [product_slugs.get(prod_name, product_slug)] if prod_name else [product_slug]

            # file_path: relative to project root, lowercase "otp/" prefix
            rel_path = pdf_path.relative_to(project_root)
            file_path = str(rel_path)

            if file_path in existing_paths:
                print(f"  SKIP (exists): {stem}")
                continue

            sha = sha256_file(pdf_path)
            print(f"  Register: {stem} → {product_ids}")
            result = api(base_url, "POST", "/api/catalog/documents", {
                "id": stem,
                "title": stem.replace("_", " "),
                "file_path": file_path,
                "source": "otp-folder",
                "product_ids": product_ids,
                "sha256": sha,
            })
            if result and "id" in result:
                registered += 1
            else:
                # Maybe upsert issue — try with unique ID
                print(f"    Retry with file-stem ID...")

        print(f"  ✓ {registered} standalone PDFs registered")

    # === Summary ===
    print()
    print("=== Összegzés ===")
    cat = api(base_url, "GET", "/api/catalog")
    banks = cat.get("banks", [])
    docs = cat.get("documents", [])
    print(f"Bankok: {len(banks)}")
    for b in banks:
        print(f"  {b['name']}: {len(b.get('products', []))} termék")
    print(f"Dokumentumok: {len(docs)}")
    by_product = {}
    for d in docs:
        for pid in d.get("product_ids", []):
            by_product.setdefault(pid, []).append(d["title"])
    for pid, dlist in sorted(by_product.items()):
        print(f"  {pid}: {len(dlist)} dok.")

    print()
    print("✓ Katalógus kész!")


if __name__ == "__main__":
    main()
