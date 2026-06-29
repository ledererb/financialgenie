import sys
from pathlib import Path
import json

# Setup import path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from ai.field_recognizer import FieldRecognizer, MappingConfig
from config import list_pdfs, mapping_path_for

def main():
    recognizer = FieldRecognizer()
    
    print("Checking for missing mapping files...")
    generated_count = 0
    
    for pdf in list_pdfs():
        pdf_id = pdf["pdf_id"]
        pdf_path = PROJECT_ROOT / pdf_id
        mpath = mapping_path_for(pdf_id)
        
        if not mpath.exists():
            print(f"\nGenerating mapping for: {pdf_id}")
            print(f"Target mapping file: {mpath.name}")
            
            try:
                # Runs auto-recognition (AcroForm widgets or flat text anchors)
                mapping_cfg = recognizer.recognize(pdf_path, mode="auto")
                
                # Ensure parent dir exists
                mpath.parent.mkdir(parents=True, exist_ok=True)
                
                # Save the mapping config
                mapping_cfg.save(mpath)
                print(f"Successfully generated and saved {len(mapping_cfg.fields)} fields.")
                generated_count += 1
            except Exception as e:
                print(f"Error generating mapping for {pdf_id}: {e}")
                
    print(f"\nDone! Generated {generated_count} new mapping files.")

if __name__ == "__main__":
    main()
