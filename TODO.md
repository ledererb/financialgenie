# TODO

## CIB PDF duplikáció (orphan widget fix)
A CIB PDF (`ertekbecslo-megrendelolap-240920.pdf`) duplikált widget struktúrával rendelkezik:
a `/AcroForm/Fields/*/Kids` alatt lévő widget objektumok **különböző objektum ID-k** mint az
oldal `/Annots` listájában lévő widget-ek, bár ugyanarra a mezőre hivatkoznak.

Az orphan widget fix ezeket a "szórvány" Kids objektumokat minden oldalhoz hozzáadja,
ami duplikációt okoz (79 widget az 1. oldalon eredetileg 34 helyett).

A Rect-alapú duplikáció elkerülés (round(1) koordináta egyezés) nem elég pontos,
mert a Kids és Annots widget-ek Rect-ei kis eltéréseket mutatnak.

**Lehetséges megoldások:**
1. Megnöelni a round pontosságot (round(0) vagy teljes Rect egyezés)
2. Field-név + Rect kombináció alapján szűrni (ha ugyanaz a field-name már van az oldalon ugyanazon a pozíción)
3. A CIB PDF pre-processzálása: törölni a `/Kids` widget-eket amiknek már van párja az `/Annots`-ban
