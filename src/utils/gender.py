"""Magyar keresztnév → nem becslő.

A Salesforce Contact.Salutation gyakran üres, miközben a banki nyomtatványok
kötelezően kérik a szereplő nemét (Nő/Férfi checkbox). Ez a modul egy
beágyazott, ékezet-független keresztnév-tábla alapján ad becslést.

A becslés NEM tekinthető CRM-adatnak — a hívó felelőssége, hogy jelezze a
felhasználó felé (notice), amikor név-alapú becslés történt.
"""

from __future__ import annotations

import unicodedata

# Gyakori magyar keresztnevek (a teljesség igénye nélkül — a cél a
# tipikus banki ügyfélnevek lefedése). Ékezet nélküli, kisbetűs alakban.
_MALE_NAMES = frozenset("""
adám aladár alajos albert alex alexander alfonz alfréd andor andrás antal arisztid arkád
armand ármin arnold arnót áron árpád artúr attila aurél balázs balint bálint barna barnabás
béla bendegúz benedek benjámin bernát bertalan boldizsár bonifác botond csaba csongor dénes
dominik domokos donát edgár edömér elemér előd emil endre erik ernő ervin ferenc flórián
gábor gáspár gellért gergely gergő géza gusztáv győző györgy gyula hunor ignác illyés imre
istván iván jenő józsef jános károly kálmán kornél krisztián lajos lászló levente loránd
lóránt marcell márk márton mátyás mihály miklós milán nandor nándor norbert olivér otto ottó
ödön pál péter rajmund rezső richárd robert róbert rómeo rudolf sandor sándor sebestyén
szabolcs szilárd szilveszter tamás tibor tivadar vencel vendel viktor vilmos zoltán zsolt
""".split())

_FEMALE_NAMES = frozenset("""
adél adrienn agnes ágnes alexandra anett anikó anita anna annamária aranka beata beáta
beatrix bernadett borbála brigitta cecília csilla dalma diána dorina dorottya edit eémma
emese emma enikő erzsébet eszter etelka eugénia éva fanni franciska gabriella gertrúd gizella
hajnal hajnalka hanna henriett hilda ildikó ilona iren irén ivett izabella jolán jozefa józsa
judit julia júlia katalin katica kinga klaudia klára krisztina laura lili lilla luca magdolna
margit marta mária mariann marianna melinda mercedesz mónika nikolett nóra olga orsolya piroska
reka réka renáta rita rozália róza rózsa sarolta szilvia tünde valéria veronika viktória viola
vivien zita zoe zója zsófia zsuzsa zsuzsanna
""".split())


def _fold(s: str) -> str:
    """Ékezet-mentes, kisbetűs alak."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s.strip().lower())
        if unicodedata.category(c) != "Mn"
    )


_MALE_NAMES = frozenset(_fold(n) for n in _MALE_NAMES)
_FEMALE_NAMES = frozenset(_fold(n) for n in _FEMALE_NAMES)


def guess_gender(full_name: str) -> str | None:
    """Teljes névből nem-becslés.

    Magyar névsorrendet feltételez (vezetéknév + keresztnév[ek]), de minden
    tokent megvizsgál, hátulról előre (a keresztnév jellemzően hátul áll).

    Returns:
        "Férfi" | "Nő" | None (ismeretlen név / üres input)
    """
    if not full_name:
        return None
    tokens = [_fold(t) for t in full_name.replace("-", " ").split() if t.strip()]
    for token in reversed(tokens):
        if token in _MALE_NAMES:
            return "Férfi"
        if token in _FEMALE_NAMES:
            return "Nő"
    return None
