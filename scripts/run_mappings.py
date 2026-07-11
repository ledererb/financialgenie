#!/usr/bin/env python3
"""
Run AI field recognition on all catalog documents.

Iterates over every unique PDF in the catalog and runs the FieldRecognizer
via the backend's /api/mapping/recognize endpoint. Skips documents that
already have a mapping (with >0 fields).

Usage:
    python3 scripts/run_mappings.py

Requires:
    - Backend running on http://localhost:8765
    - ANTHROPIC_API_KEY set in config/.env
"""
import sys
import time
import requests
from pathlib import Path

API = "http://127.0.0.1:8765"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main():
    # Fetch the catalog
    resp = requests.get(f"{API}/api/catalog")
    resp.raise_for_status()
    catalog = resp.json()

    docs = catalog.get("documents", [])
    print(f"Catalog: {len(docs)} documents")

    # Collect unique PDFs (by file_path)
    seen = set()
    unique_pdfs = []
    for doc in docs:
        fp = doc.get("file_path", "")
        if not fp or fp in seen:
            continue
        seen.add(fp)
        # Normalize: if absolute path, make relative to project root
        if fp.startswith("/"):
            try:
                fp = str(Path(fp).relative_to(PROJECT_ROOT))
            except ValueError:
                pass
        unique_pdfs.append({"pdf_id": fp, "title": doc["title"]})

    print(f"Unique PDFs to map: {len(unique_pdfs)}")
    print()

    results = []
    for i, pdf in enumerate(unique_pdfs, 1):
        pdf_id = pdf["pdf_id"]
        title = pdf["title"]
        print(f"[{i}/{len(unique_pdfs)}] {title[:50]}...", end=" ", flush=True)

        # Check if mapping already exists
        try:
            mresp = requests.get(
                f"{API}/api/mapping",
                params={"pdf_id": pdf_id},
                timeout=10,
            )
            existing = mresp.json()
            if existing.get("fields") and len(existing["fields"]) > 0:
                print(f"SKIP (already has {len(existing['fields'])} fields)")
                results.append({"title": title, "status": "skipped", "fields": len(existing["fields"])})
                continue
        except Exception:
            pass

        # Start recognition
        try:
            rresp = requests.post(
                f"{API}/api/mapping/recognize",
                params={"pdf_id": pdf_id},
                json={"mode": "auto"},
                timeout=30,
            )
            if rresp.status_code == 503:
                print("SKIP (recognizer unavailable)")
                results.append({"title": title, "status": "unavailable"})
                continue
            if rresp.status_code != 200:
                print(f"ERROR (HTTP {rresp.status_code})")
                results.append({"title": title, "status": "error", "error": f"HTTP {rresp.status_code}"})
                continue

            task_id = rresp.json().get("task_id")
            if not task_id:
                print("ERROR (no task_id)")
                results.append({"title": title, "status": "error", "error": "no task_id"})
                continue

            # Poll for completion
            done = False
            for attempt in range(120):  # 120 × 3s = 6 min max per PDF
                time.sleep(3)
                try:
                    sresp = requests.get(
                        f"{API}/api/recognize/{task_id}/status",
                        timeout=10,
                    )
                    status = sresp.json()
                except Exception:
                    continue

                if status.get("status") == "done":
                    field_count = len(status.get("result", {}).get("fields", []))
                    print(f"DONE ({field_count} fields)")
                    results.append({"title": title, "status": "done", "fields": field_count})
                    done = True
                    break
                elif status.get("status") == "error":
                    print(f"ERROR ({status.get('error', 'unknown')})")
                    results.append({"title": title, "status": "error", "error": status.get("error")})
                    done = True
                    break

            if not done:
                print("TIMEOUT")
                results.append({"title": title, "status": "timeout"})

        except Exception as e:
            print(f"EXCEPTION ({e})")
            results.append({"title": title, "status": "exception", "error": str(e)})

    # Summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    done = [r for r in results if r["status"] == "done"]
    skipped = [r for r in results if r["status"] == "skipped"]
    errors = [r for r in results if r["status"] in ("error", "exception", "timeout")]
    print(f"  Done:     {len(done)}")
    print(f"  Skipped:  {len(skipped)}")
    print(f"  Errors:   {len(errors)}")
    if errors:
        print("\nErrors:")
        for r in errors:
            print(f"  {r['title']}: {r.get('error', r['status'])}")


if __name__ == "__main__":
    main()
