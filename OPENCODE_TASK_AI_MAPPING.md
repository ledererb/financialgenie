# OpenCode Task: AI-Driven Dynamic PDF Mapping (V4 Pro)

## Cél
A pipeline legyen képes **bármelyik PDF-et dinamikusan bemappelni**, ne csak azokat amikhez van előre elkészített mapping JSON. Ehhez a FieldRecognizer DeepSeek V4 Pro-val dolgozik futási időben.

## Repository
`/home/openclaw/.openclaw/workspace/financialgenie/`

## Mit kell csinálni

### 1. field_recognizer.py – V4 Pro + dynamic classification metódus

File: `src/ai/field_recognizer.py`

**a) Változtasd az AI_MODEL-t V4 Pro-ra:**
```python
AI_MODEL: str = "deepseek-v4-pro"
```

**b) Add hozzá a `dynamic_classify_fields()` metódust:**
```python
def dynamic_classify_fields(
    self,
    fields: list[RecognizedField],
    canonical_model_fields: list[str],
    salesforce_field_descriptions: dict[str, str],
    pdf_name: str = ""
) -> list[RecognizedField]:
    """
    FUTÁSI IDEJŰ mezőklasszifikáció DeepSeek V4 Pro-val.
    
    Bemenet: ismeretlen PDF mezők (label, típus, oldalszám)
    Kimenet: canonical_field-dal feltöltött RecognizedField-ek
    
    NEM küld PDF képeket – tisztán szöveges, gyors, olcsó.
    100-200 mező egy batch-ben feldolgozható.
    """
```

**Prompt terv:**
```
System: "Te egy banki nyomtatvány mező-klasszifikációs AI vagy.
A feladatod: az alábbi PDF mezőkhöz rendeld hozzá a megfelelő canonical 
mezőt az alábbi listából. 
Kanonikus mezők:
{canonical_fields}

Salesforce elérhető adatok:
{salesforce_descriptions}

Válaszul csak egy JSON tömböt adj: 
[{"pdf_field_name": "...", "canonical_field": "..."}]
Ahol nem egyértelmű, hagyd ki (ne tippelj LOW confidence-szel)."

User: felsorolja a mezőket: név, típus, label, oldal, (opcionális: kontextus szöveg a PDF-ből)
```

**c) Add hozzá a `run_dynamic_mapping()` metódust:**
Ez a magas szintű metódus, amit a pipeline hív:
```python
def run_dynamic_mapping(
    self,
    pdf_path: Path,
    fields_to_map: list[dict],
    deal_data: 'DealData'
) -> dict[str, str]:
    """
    Teljes dinamikus mapping workflow:
    1. Szöveges label-ek kinyerése a PDF-ből (PASS 1: exact match)
    2. Salesforce data-first matching (PASS 2: mi van adatban → hova megy a PDF-ben)
    3. AI klasszifikáció (PASS 3: DeepSeek V4 Pro)
    
    Returns: {pdf_field_name: canonical_field} dict
    """
```

### 2. pass2_mapping.py – Valódi AI sub-pass implementáció

File: `src/pipeline/pass2_mapping.py`

A jelenlegi stub sub-pass 3-at cseréld ki valódi implementációra:

```python
# --- Sub-pass 3: AI classification (dynamic V4 Pro) ---
if run_ai_subpass and not has_static_mapping:
    logger.info("🤖 Pass 2 (mapping): sub-pass 3 – AI klasszifikáció (V4 Pro)")
    
    # 1. Kinyerni a PDF mezőket az analyze_pdf segítségével
    from scripts.analyze_pdf import analyze_pdf_fields
    all_pdf_fields = analyze_pdf_fields(...)
    
    # 2. Meghatározni a canonical mezőket amikhez van adat
    available_fields = get_available_salesforce_fields(deal)
    
    # 3. FieldRecognizer dinamikus klasszifikációja
    recognizer = FieldRecognizer()
    classified = recognizer.dynamic_classify_fields(
        fields=unmapped_pdf_fields,
        canonical_model_fields=available_fields,
        salesforce_field_descriptions=sf_descriptions,
        pdf_name=mapping.form_name
    )
    
    # 4. Eredmények hozzáadása a field_data-hoz
    for f in classified:
        if f.canonical_field:
            value = extract_value_from_deal(deal, f.canonical_field)
            if value:
                field_data[f.pdf_field_name] = {
                    'value': value,
                    'width': original_width,
                    ...
                }
```

**FONTOS:**
- Az AI sub-pass csak akkor fusson, ha nincs pre-built mapping VAGY ha a coverage 80% alatt van
- Ne küldjön PDF képeket az AI-nak – csak szöveges mezőneveket + label-eket
- Batch méret: max 100 mező/hívás

### 3. PipelineOrchestrator – `--dynamic-mapping` flag

File: `src/pipeline/orchestrator.py` és `src/main.py`

Add hozzá a `--dynamic-mapping` / `--ai` kapcsolót, ami:
1. Betölti a mapping JSON-t ha létezik
2. Ha nem létezik: üres mapping-gel indul
3. Minden esetben: `run_ai_subpass=True`-ként fut
4. Coverage threshold: 80%

```python
# main.py cli
parser.add_argument('--dynamic-mapping', action='store_true',
    help='AI-alapú dinamikus mapping (nincs szükség előre elkészített JSON-ra)')
parser.add_argument('--ai-model', default='deepseek-v4-pro',
    help='Modell az AI klasszifikációhoz (default: deepseek-v4-pro)')
```

### 4. Tesztelés

Futtasd az értékbecslés PDF-en dinamikus mappinggel:
```bash
cd /home/openclaw/.openclaw/workspace/financialgenie
python3 src/main.py --all --salesforce --deal 006WB00000LwTDgYAN --dynamic-mapping
```

Hasonlítsd össze az eredményt a statikus mapping-gel:
```bash
python3 src/verify_fill.py output/filled_006WB00000LwTDgYAN_*.pdf
```

### 5. Commit + Push
```bash
git add -A
git commit -m "feat: AI-driven dynamic PDF mapping with DeepSeek V4 Pro"
git push origin main
```

### 6. Report Slackben
```bash
curl -s -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "C0BDN6263SS",
    "text": "✅ *AI-driven dynamic mapping implemented*\n• V4 Pro: DeepSeek V4 Pro minden AI hívásnál\n• Dynamic classification: ismeretlen PDF mezők valós idejű felismerése\n• --dynamic-mapping flag: mapping JSON nélkül is működik\n• 3-pass: exact match → Salesforce data-first → AI V4 Pro\n• Committed: <hash>"
  }'
```

## Miért V4 Pro?
- Több kontextus (64K vs 8K)
- Jobb JSON mód
- Pontosabb label felismerés magyar szövegen
- Lassabb de megbízhatóbb – erre a feladatra kell

## Mit NEM csinálj
- Ne változtasd meg a meglévő mapping JSON formátumot
- Ne töröld a meglévő statikus mapping-eket
- Ne változtasd a canonical_model.py-t
- Ne cseréld le a komplett pipeline architektúrát – csak add hozzá a dinamikus ágat
