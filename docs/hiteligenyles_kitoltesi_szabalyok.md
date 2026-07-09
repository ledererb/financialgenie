# Hiteligénylési nyomtatvány – Kitöltési szabályrendszer

**Státusz:** munkaanyag / draft logika a fill engine-hez
**Cél:** a `Fixen_töltött mezők.docx` alapján azonosított kitöltési minták általánosítása, keretprogramtól (ALAP, CSOK Plusz, Otthon Start) függetlenül — hogy a rendszer bármilyen jövőbeni keretre is alkalmazható legyen ugyanazzal a szabálymotorral.

---

## Alapfogalmak

- **Szereplő**: az ügyletben részt vevő fél (pl. Adós, Adóstárs/Társigénylő, egyéb ügyleti fél). Forrás: Salesforce **Contact Role**.
- **Blokk**: egy adott pont(kérdés) nyomtatványon belüli almezőcsoportja (pl. "igen/nem" rubrikák, felsorolás soronként).
- **Pont**: a nyomtatvány egy sorszámozott kérdése/checkbox-csoportja.

Minden azonosított minta egy **általános szabálytípusra** vezethető vissza. A konkrét pontszámok (ALAP 9., CSOK 3-4. stb.) csak **példák/instanciák** az adott szabálytípusra — maga a logika keretprogramtól független.

---

## Szabálytípusok

### 1. Alapértelmezett, teljes körű pipázás
**Leírás:** A pont minden, az ügyletben szereplő félre vonatkozik, feltétel nélkül.

**Logika:**
```
HA pont == "feltétel nélküli" ÉS nincs speciális jelölés
 → minden ügyleti szereplő bepipálásra kerül
```

**Megjegyzés:** Ez a leggyakoribb minta — a forrásdokumentumban vizsgált pontok többsége ide tartozik, függetlenül attól, hogy melyik keretben szerepelnek.

---

### 2. Szerepkör-alapú, többszörös pipázás
**Leírás:** Egy szereplő a saját alapszerepe *mellett* más szerepkörben is megjelenhet a nyomtatványon, és mindkét (vagy több) szerepéhez tartozó jelölőnégyzet kitöltésre kerül.

**Logika:**
```
HA szereplő.szerep == "Adós"
 → pipa: "Adós" ÉS pipa: "Fedezettulajdonos(1)"

HA szereplő.szerep == "Adóstárs / Társigénylő"
 → pipa: "Adóstárs/Társigénylő" ÉS pipa: "Fedezettulajdonos(2)"
```

**Forrás:** SF **Contact Role** mező határozza meg, mely szerepek aktívak egy adott szereplőnél.

**Megjegyzés:** Egy szereplő így **egynél több pipát** is kaphat ugyanazon a nyomtatványrészen belül.

---

### 3. Részleges blokk-pipázás (csak egy alblokk)
**Leírás:** A pont több almezőt/blokkot tartalmaz, de a szabály csak **egy konkrét alblokkra** vonatkozik — a többi blokk üresen marad.

**Logika:**
```
HA pont.blokkok.length > 1
 → csak: blokk[N].rubrika == "igen" minden szereplőre
 → a többi blokk: nincs pipa
```

**Megjegyzés:** Fontos elkülöníteni a 4-es típustól (ott ugyanis *minden* blokk kap valamilyen jelölést, csak eltérőt).

---

### 4. Kétlépéses / összetett pipázás
**Leírás:** A pont két, egymást követő lépésből áll: (1) egy alapérték minden blokkban, majd (2) a pont *végén* egy kiegészítő, szerepkör szerinti pipa.

**Logika:**
```
LÉPÉS 1: minden blokkban → "nem vagyok" (vagy adott esetben konkrét checkbox szövege) minden szereplőre
LÉPÉS 2 (a pont végén): minden szereplő szerepe mellé → pipa
```

**Megjegyzés:** Két azonosított instancia van erre a mintára a forrásban (eltérő konkrét szöveggel az 1. lépésben), ami arra utal, hogy ez egy visszatérő, önálló mintaként kezelendő logika, nem kivétel.

---

### 5. Termékfüggő feltételes pipázás
**Leírás:** A pont csak akkor kerül kitöltésre, ha az ügyfél egy adott **terméktípust** választott.

**Logika:**
```
HA ügyfél.termék == "<adott termék>"
 → minden ügyleti szereplő bepipálásra kerül
KÜLÖNBEN
 → nincs pipa
```

**Forrás:** SF termék mező (pontos mezőnév/érték tisztázandó — lásd Nyitott kérdések).

---

### 6. Hitelcél-függő feltételes pipázás
**Leírás:** A pont csak akkor kerül kitöltésre, ha az ügyfél által megjelölt **hitelcél** egy adott értéknek felel meg.

**Logika:**
```
HA SF.Hitelcél == "<adott hitelcél, pl. új ingatlan vásárlás>"
 → minden ügyleti szereplő bepipálásra kerül
KÜLÖNBEN
 → nincs pipa
```

**Forrás:** SF **Hitelcél** mező.

**Megjegyzés:** Szerkezetileg azonos az 5-ös típussal, csak más a vizsgált forrásmező (termék vs. hitelcél) — érdemes lehet a fill engine-ben egy közös, paraméterezhető "feltételes mező alapján" szabálytípusként kezelni mindkettőt.

---

### 7. Többblokkos, blokkonként eltérő pipázási minta
**Leírás:** A pont több, egymástól független alblokkból áll, és **mindegyik blokknak saját, önálló kitöltési szabálya van** — nem csak egy blokk aktív (mint a 3-as típusnál), hanem mindegyik, csak eltérő logikával.

**Logika:**
```
blokk[1] → minden szereplőre: "hozzájárulok"
blokk[2] → minden szereplőre: "igen"
blokk[3] → minden szereplőre: 1. sor
```

**Megjegyzés:** Ez a legösszetettebb azonosított minta; érdemes a fill engine-ben blokkonként külön szabálysorként (nem egyetlen ponthoz kötött logikaként) kezelni.

---

## Döntési fa (összefoglaló)

```
Pont vizsgálata
│
├─ Van feltétel (termék / hitelcél)?
│ ├─ IGEN → Szabály 5 vagy 6 (feltételes teljes pipázás)
│ └─ NEM → tovább
│
├─ Több, egymástól eltérő logikájú blokk van a ponton belül?
│ ├─ IGEN → Szabály 7 (blokkonként eltérő minta)
│ └─ NEM → tovább
│
├─ Csak egy konkrét alblokk aktív, a többi üres?
│ ├─ IGEN → Szabály 3 (részleges blokk)
│ └─ NEM → tovább
│
├─ Két lépésből áll (alapérték + záró szerepkör-pipa)?
│ ├─ IGEN → Szabály 4 (kétlépéses)
│ └─ NEM → tovább
│
├─ A szereplő szerepe alapján több pipa is jár?
│ ├─ IGEN → Szabály 2 (szerepkör-alapú többszörös)
│ └─ NEM → Szabály 1 (alapértelmezett, teljes körű)
```

---

## Nyitott kérdések / tisztázandó pontok

1. **Szabály 5 (termékfüggő)**: pontos SF mezőnév és érték az adott termék azonosításához.
2. **Szabály 6 (hitelcél-függő)**: mi történik, ha a hitelcél *nem* egyezik a feltétellel — biztosan nincs semmilyen pipa, vagy más blokk/logika lép életbe helyette?
3. Vannak-e a jelenleg **Szabály 1**-be sorolt pontok között olyanok, amelyeknél valójában rejtett feltétel (termék- vagy hitelcél-függés) áll fenn, csak a forrásjegyzet nem jelezte külön?
4. Szabály 3 és Szabály 7 elhatárolása minden esetben egyértelmű-e, vagy létezik átmeneti eset (pl. 2 aktív blokk 1 helyett 3-ból)?
5. Az itt azonosított 7 szabálytípus lefedi-e a teljes nyomtatványt, vagy a nem vizsgált oldalakon/pontokon várhatóan új minták is felbukkannak?

---

## Megjegyzés a felhasználásról

Ez a szabályrendszer **keretprogram-független**: egy adott konkrét pont (pl. "ALAP 8. pont" vagy "CSOK Plusz 14. pont") a fill engine-ben egy **(pont azonosító → szabálytípus + paraméterek)** hozzárendelésként reprezentálható, pl.:

```json
{
 "point_id": "ALAP_8",
 "rule_type": 5,
 "condition_field": "SF.Termék",
 "condition_value": "évnyerő"
}
```

Így az egyes keretek (ALAP, CSOK Plusz, Otthon Start, illetve jövőbeni új keretek) mindegyike ugyanazt a 7 szabálytípust használja, csak eltérő pont–szabály hozzárendeléssel.
