# FinancialGenie – Banki nyomtatványkitöltő PoC

Salesforce-ból indított, determinisztikus PDF-kitöltő automatizáció.  
AI (Claude Sonnet 4.6) kizárólag új nyomtatványok mezőfelismerésére szolgál.

## Telepítés

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Konfiguráció

```bash
cp config/.env.example config/.env
# Töltsd ki az API kulcsokat
```

## Használat

```bash
# Minta PDF-ek generálása
python scripts/generate_sample_pdfs.py

# Dummy adatok generálása
python scripts/generate_dummy_data.py

# AI mezőfelismerés futtatása
python -m src.ai.field_recognizer samples/acroform_sample.pdf

# Teljes pipeline futtatása
python -m src.main
```

## Tesztek

```bash
pytest tests/ -v --cov=src
```
