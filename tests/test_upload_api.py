import pytest
from fastapi.testclient import TestClient
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from server import app

client = TestClient(app)

def test_upload_and_download_endpoints(tmp_path):
    # 1. Prepare a dummy PDF to upload
    acroform_src = PROJECT_ROOT / "samples" / "acroform_sample.pdf"
    if not acroform_src.exists():
        pytest.skip("acroform_sample.pdf is missing")
        
    temp_pdf_upload = tmp_path / "api_test_upload.pdf"
    shutil.copy(acroform_src, temp_pdf_upload)
    
    # 2. Upload the PDF file
    with open(temp_pdf_upload, "rb") as f:
        response = client.post(
            "/api/pdf/upload",
            files={"file": ("api_test_upload.pdf", f, "application/pdf")}
        )
        
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["success"] is True
    assert "pdf_id" in res_data
    assert "filled_pdf_url" in res_data
    
    pdf_id = res_data["pdf_id"]
    filled_pdf_url = res_data["filled_pdf_url"]
    
    # Verify the uploaded PDF file exists in samples/
    saved_pdf_path = PROJECT_ROOT / pdf_id
    assert saved_pdf_path.exists()
    
    # Verify mapping file was created in src/mapping/
    stem = Path(pdf_id).stem
    expected_mapping = PROJECT_ROOT / "src" / "mapping" / f"{stem}_mapping.json"
    assert expected_mapping.exists()
    
    # 3. Test downloading the filled PDF
    # The URL looks like /api/pdf/download?path=...
    download_res = client.get(filled_pdf_url)
    assert download_res.status_code == 200
    assert download_res.headers["content-type"] == "application/pdf"
    
    # 4. Test download security check (directory traversal)
    bad_url = "/api/pdf/download?path=/etc/passwd"
    bad_res = client.get(bad_url)
    assert bad_res.status_code == 403
    
    # Clean up generated files
    if saved_pdf_path.exists():
        saved_pdf_path.unlink()
    if expected_mapping.exists():
        expected_mapping.unlink()
