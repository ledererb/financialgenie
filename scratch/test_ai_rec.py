import sys
from pathlib import Path

# Setup import path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ai.field_recognizer import FieldRecognizer

def main():
    print("Initializing FieldRecognizer...")
    recognizer = FieldRecognizer()
    print("Client initialized:", bool(recognizer._client))
    if not recognizer._client:
        print("Error: Anthropic client could not be initialized.")
        return
        
    pdf_path = PROJECT_ROOT / "samples" / "flat_sample.pdf"
    print(f"Recognizing {pdf_path.name}...")
    try:
        # We run the flat pdf recognition which calls _ai_recognize_flat_pdf
        mapping = recognizer.recognize(pdf_path, mode="overlay")
        print("\nSuccess! AI Recognition Result:")
        print(f"Form Name: {mapping.form_name}")
        print(f"Total Fields: {len(mapping.fields)}")
        mapped = sum(1 for f in mapping.fields if f.canonical_field)
        print(f"Mapped Fields: {mapped}")
        for f in mapping.fields[:10]:
            print(f"  - Field: {f.pdf_field_name} | Label: {f.label} -> Canonical: {f.canonical_field} (Confidence: {f.confidence})")
    except Exception as e:
        print("Recognition failed:", e)

if __name__ == "__main__":
    main()
