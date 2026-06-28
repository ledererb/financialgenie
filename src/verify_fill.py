#!/usr/bin/env python3
"""
Self-verification script for FinancialGenie filled PDFs.

Usage:
    python3 src/verify_fill.py <filled_pdf_path>

Returns exit code 0 on success, 1 on failure.
Prints detailed verification report.
"""

import sys, os, json, pikepdf

EXPECTED_CHECKS = {
    # TF 357-362: should NOT contain address text (was the "residence years" bug)
    "residence_since_not_address": {
        "fields": ["Text Field 357", "Text Field 358", "Text Field 359",
                    "Text Field 360", "Text Field 361", "Text Field 362"],
        "forbidden": ["Budapest", "Alkotás", "Kecskemét", "út"],  # address-like text
        "description": "TF 357-362 should not contain address text (residence years fix)",
    },
    # Overflow protection: single digit boxes should NOT have multi-char values
    "overflow_single_boxes": {
        "fields": ["Text Field 1179", "Text Field 1221", "Text Field 1237",
                    "Text Field 330", "Text Field 334", "Text Field 335",
                    "Text Field 340", "Text Field 341"],
        "max_len": 1,
        "description": "Overflow single digit boxes should not get multi-char values",
    },
    # Loan amount digit boxes: should be individual digits
    "loan_digit_boxes": {
        "fields": ["Text Field 1181", "Text Field 1182", "Text Field 1183",
                    "Text Field 1184", "Text Field 1185", "Text Field 1186",
                    "Text Field 1187", "Text Field 1188"],
        "min_len": 1,
        "max_len": 1,
        "all_digits": True,
        "description": "Loan amount digit boxes should be single characters",
    },
    # Income digit boxes: should be individual digits
    "income_digit_boxes": {
        "fields": ["Text Field 412", "Text Field 413", "Text Field 414",
                    "Text Field 415", "Text Field 416", "Text Field 417"],
        "min_len": 1,
        "max_len": 1,
        "all_digits": True,
        "description": "Income digit boxes should be single digits",
    },
    # Phone digit boxes: should be individual digits
    "phone_digit_boxes": {
        "fields": ["Text Field 346", "Text Field 347", "Text Field 348",
                    "Text Field 349", "Text Field 350", "Text Field 351",
                    "Text Field 352", "Text Field 353", "Text Field 354",
                    "Text Field 355", "Text Field 356"],
        "min_len": 1,
        "max_len": 1,
        "all_digits": True,
        "description": "Phone digit boxes should be single digits",
    },
}


def verify_pdf(pdf_path: str) -> dict:
    """Verify a single filled PDF against expected checks."""
    results = {
        "pdf": os.path.basename(pdf_path),
        "total_filled": 0,
        "checks": {},
        "all_passed": True,
    }

    with pikepdf.open(pdf_path) as pdf:
        fields = pdf.Root["/AcroForm"]["/Fields"]
        vals = {}
        def collect(items):
            for f in items:
                name = str(f.get("/T", ""))
                if "/V" in f:
                    v = str(f["/V"])
                    vals[name] = v
                if "/Kids" in f:
                    collect(f["/Kids"])
        collect(fields)

    results["total_filled"] = len(vals)

    for check_name, check in EXPECTED_CHECKS.items():
        check_result = {
            "passed": True,
            "description": check["description"],
            "field_results": {},
        }

        for field_name in check["fields"]:
            val = vals.get(field_name)
            field_result = {"value": val, "issues": []}

            if val is None:
                # Empty/missing field. For "should not" checks (forbidden/max_len only,
                # no min_len requirement), an empty field PASSES — it has no bad content.
                if "min_len" in check:
                    field_result["issues"].append("FIELD NOT FOUND")
                # else: empty is acceptable for forbidden/max_len checks
            else:
                # Check forbidden values
                if "forbidden" in check:
                    for forbidden in check["forbidden"]:
                        if forbidden.lower() in val.lower():
                            field_result["issues"].append(
                                f"Contains forbidden text '{forbidden}'"
                            )

                # Check length
                if "min_len" in check and len(val) < check["min_len"]:
                    field_result["issues"].append(
                        f"Value too short: {len(val)} < {check['min_len']}"
                    )
                if "max_len" in check and len(val) > check["max_len"]:
                    field_result["issues"].append(
                        f"Value too long: {len(val)} > {check['max_len']}"
                    )

                # Check digits only
                if check.get("all_digits") and val and not val.isdigit():
                    field_result["issues"].append(
                        f"Value contains non-digit characters: '{val}'"
                    )

            if field_result["issues"]:
                check_result["passed"] = False
                check_result["field_results"][field_name] = field_result

        if check_result["passed"]:
            check_result["field_results"]["summary"] = "ALL OK"
        else:
            results["all_passed"] = False

        results["checks"][check_name] = check_result

    return results


def print_report(results: dict):
    """Pretty-print verification report."""
    print(f"\n{'='*60}")
    print(f"  VERIFICATION REPORT: {results['pdf']}")
    print(f"  Total fields filled: {results['total_filled']}")
    print(f"{'='*60}")

    all_ok = True
    for check_name, check_result in results["checks"].items():
        status = "✅" if check_result["passed"] else "❌"
        print(f"\n  {status} {check_name}")
        print(f"     {check_result['description']}")

        if check_result["passed"]:
            continue

        all_ok = False
        for field_name, field_result in check_result["field_results"].items():
            if field_name == "summary":
                continue
            val_display = field_result.get("value", "NONE") or "NONE"
            print(f"     ✗ {field_name}: '{val_display}'")
            for issue in field_result["issues"]:
                print(f"       - {issue}")

    print(f"\n{'='*60}")
    if results["all_passed"]:
        print("  VERDICT: ✅ ALL CHECKS PASSED")
        return 0
    else:
        print("  VERDICT: ❌ SOME CHECKS FAILED")
        return 1


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 src/verify_fill.py <filled_pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)

    results = verify_pdf(pdf_path)
    ec = print_report(results)
    sys.exit(ec)


if __name__ == "__main__":
    main()
