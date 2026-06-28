# Dokumentumfeldolgozó tanulságok

## Mi történt eddig?

7 PDF-en teszteltük a pipeline-t. 3 teljesen különböző típuson ment keresztül:

### Típus 1: Jól elnevezett AcroForm mezők
**PDF:** OTP v5 master (1729 mező), V függelék (90 mező)
**Mapping strategy:** 1:1 név alapján
**Eredmény:** Magabiztos, determinisztikus — ami benne van a mapping-ben, az megy. 89/1729 (+ legal checkboxok).

**Tanulság:** Ez a típus a legegyszerűbb. A mezőnevek beszédesek (`SZA_IG_név`, `-társ` suffix), a mapping statikus JSON-ban karbantartható. A limitáció nem a feldolgozóban van, hanem hogy a Salesforce adatmodell mennyit fed le a PDF mezőiből.

---

### Típus 2: Generikus AcroForm mezőnevek
**PDF:** Előzetes értékbecslés (1029 mező, mind "Check Box NNN" / "Text Field NNN")
**Kipróbált stratégiák:**
1. **Page-by-page DeepSeek klasszifikáció** → 96/1029 mapped (9.3%)
   - Pro: működik egyértelmű label-eknél
   - Kontra: drága (1029 mező = 26 oldal × ~40 mező/batch = API callok), és a homályos kontextust sem tudja
2. **Salesforce label matching** → 248/1029 mapped (24.1%)
   - Pro: ingyenes (nincs API hívás), precíz ha van label
   - Kontra: csak annyit talál amennyi adat a Salesforce-ban van
3. **Nem próbáltuk:** emberi beazonosítás oldalanként → ez a legpontosabb, de skálázhatatlan

**Tanulság:** A **Salesforce-first** stratégia a leghatékonyabb. Ahelyett hogy vakon klasszifikálnánk, a Salesforce-ból indulunk ki: "mi az ami adat van, és hol van a PDF-ben a helye?". Ezután a maradékot AI klasszifikációval.

---

### Típus 3: Lapos PDF (nincs AcroForm)
**PDF:** Partner nyilatkozat, Hozzájáruló nyilatkozat, Zöld Lakáshitel, CSOK/ÁFA
**Megoldás:** PyMuPDF placeholder detektálás (underlines, `____`, `□`) → OverlayFiller TextPlacement koordinátákkal
**Eredmény:** Működik, de minden placement-hez külön canonical_field kell a mapping-ben.

**Tanulság:** Lapos PDF-eknél nincs varázslat — minden egyes `____` helyet be kell azonosítani pozíció alapján. A `scripts/analyze_flat_pdf.py` megcsinálja a detektálást, de a canonical hozzárendelés emberi/DeepSeek döntés.

---

## Így KELLENE működnie a dokumentumfeldolgozónak

### 1. PDF analízis réteg (determinisztikus)
Egy `analyze_pdf.py` ami minden PDF-ről megmondja:
- Van-e AcroForm? Ha igen: mezőnevek, típusok, pozíciók
- Ha nincs: hol vannak `____`, `...`, `□` placeholder-ek
- Page-enkénti szövegblokkok pozíciókkal (címkék kereséséhez)

Ez egyszer fut le PDF-enként, és kiment egy JSON-t: `{field_name, type, page, x, y, surrounding_text, label}`.

### 2. Mapping pipeline — 4 passzos

**Pass 1 — Exact match (0 AI cost)**
A mezőnevek egy-az-egyhez illesztése a canonical modellhez. Ha a PDF neve beszédes → kész. Gyors, olcsó.

**Pass 2 — Salesforce label matching (0 AI cost)**
Salesforce-ból kinyert adatok → PDF címkék → mezők. A canonical modellből indulunk ki (mi van adatban), és a PDF-ben megkeressük a hozzá tartozó mezőt.

**Pass 3 — AI klasszifikáció (DeepSeek, alacsony cost)**
A pass 1-2 után megmaradt ismeretlen mezők batch klasszifikációja DeepSeek-el. Csak a valóban ismeretlenekre, nem az összesre.

**Pass 4 — AI legal checkbox defaults (DeepSeek / rule-based)**
A checkbox-oknál jogi kategória meghatározás + default igen/nem. Utolsó pass, mert a legtöbb ilyen "elnémítható" — a dokumentum működik nélkülük.

### 3. Orchester réteg
A 4 pass egymás után fut:
```
analyze_pdf → exact_match → salesforce_match → ai_classify → legal_defaults
```
Minden pass után: `{mapped, unmapped, coverage%}` — ha a coverage elér egy threshold-öt (pl. 80%), a további AI pass-ok kihagyhatók.

### 4. Canonical modell bővítés
A jelenlegi modell (participant, property, deal, legal) elég a PoC-hoz, de élesben kell:
- `participant.co_borrower` — explicit adóstárs model
- `employer.*` — munkáltatói adatok
- `income.*` — jövedelem részletek
- `property.*` — több ingatlan
- `declaration.*` — egyéb nyilatkozatok

### 5. Az AI szerepe
**Ne az AI legyen a mapping.** Az AI csak a hiányt pótolja:
- A pass 1-2 determinisztikus (0 hallucináció)
- A pass 3-4 AI, de csak a maradék 10-30%-ra
- A legal classifier prompt `src/ai/legal_classifier_prompt.txt` jó template

---

## Konkrét következő lépések

1. **Salesforce adatmodell kiterjesztés** — több mező a SOQL-ben (már elkezdve a `salesforce_client.py` bővítéssel)
2. **`analyze_pdf.py`** — generikus PDF analizáló (AcroForm + flat + pozíciók + címkék)
3. **Passzok szétválasztása** — a jelenleg monolit `main.py` helyett modularizált pipeline
4. **Coverage tracking** — minden pass után coverage metrika, threshold alapján AI ki/bekapcsolás
5. **Architecture update** — ARCHITECTURE.md frissítése a 4-passos modellel

---

## Statisztikák (2026-06-28)

| Típus | Össz | Mapped | % | Coverage |
|-------|------|--------|---|----------|
| OTP v5 (AcroForm) | 1729 | 89 | 5.1% | Salesforce limit |
| V függelék (AcroForm) | 90 | 29 | 32.2% | Salesforce limit |
| Partner nyilatkozat (lapos) | 6 | 3 | 50% | Erősíthető |
| Hozzájáruló (lapos) | 23 | 21 | 91.3% | ✅ |
| Zöld Lakáshitel (lapos) | 49 | 28 | 57.1% | Javítható |
| CSOK/ÁFA (lapos) | 13 | 13 | 100% | ✅ |
| Értékbecslés (generikus) | 1029 | 248 | 24.1% | AI + label match |
| **Összesen** | **2939** | **431** | **14.7%** | |

A 2508 unmapped mezőből:
- ~1600 OTP v5 mező → Salesforce adatmodell bővítéssel javítható
- ~781 értékbecslés mező → label matching + AI javítható
- ~64 flat PDF mező → kézi mappingolható
- ~61 V függelék mező → Salesforce adatmodell bővítéssel javítható

**A rendszer MŰKÖDIK, de a coverage-t a Salesforce adatmodell korlátozza, nem a feldolgozó.**
