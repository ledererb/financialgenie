"""
FinancialGenie – AI + config hibrid jogi nyilatkozat klasszifikátor

A canonical_field nélküli (leképezetlen) checkbox-okat két lépésben kezeli:

1. AI kategorizáló réteg – DeepSeek V3 (deepseek-chat) besorolja a mezőket
   kategóriákba (consent, data_sharing, decline, conditional, stb.) a mező NEVE +
   LABEL + oldal alapján. Batch módban dolgozik (100-200 mező / hívás).

2. Config réteg – a legal_defaults.json alapján kategória → true/false érték.
   A conditional_rules a DealData.products alapján dönt.

Fallback: ha nincs DEEPSEEK_API_KEY, szabályalapú kulcsszó-illesztés
(classify_rule_based).

Ez a modul NEM módosítja a field_recognizer.py mapping pipeline-ját –
a jogi nyilatkozat kitöltés a canonical leképezés MELLÉ jön.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Optional

from src.models.canonical_model import DealData

logger = logging.getLogger(__name__)


# Alapértelmezett prompt template helye (a mapping könyvtárban).
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "mapping" / "legal_classifier_prompt.txt"
)
DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "mapping" / "legal_defaults.json"
)

#: Batch méret – hány mező legyen egy DeepSeek hívásban.
BATCH_SIZE = 150


def _normalize(s: str) -> str:
    """Ékezet- és kisbetű-mentesített szöveg a kulcsszó-illesztéshez."""
    if not s:
        return ""
    nfd = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in nfd if not unicodedata.combining(ch)).lower()


class LegalClassifier:
    """
    AI + config hibrid: jogi nyilatkozatok automatikus kitöltése.

    DeepSeek V3 (deepseek-chat) modellt használ chat completion formátumban,
    közvetlen HTTP POST-tal (requests) az api.deepseek.com végpontra.
    """

    # Szabályalapú kulcsszó → kategória tábla (a classify_rule_based fallback-hez).
    # A sorrend számít: a korábbi szabályok felülírják a későbbieket.
    RULE_TABLE = [
        # decline – mindig a legelső, hogy a "nem járulok hozzá" nyerjen a
        # sima "hozzájárulok" ellenében.
        ("decline", ["nem jarulok", "nem jovulok", "lemond", "elutasit", "nincs hozzajarulas"]),
        # conditional – termékfüggő
        ("conditional", ["csok", "csokplusz", "csok_plusz", "afa", "kamattamogatas", "babavarok", "zold"]),
        # data_sharing
        ("data_sharing", ["adatkezeles", "adattovabbitas", "gdpr", "adatvedelem", "hirlevel"]),
        # property_state
        ("property_state", ["tulajdoni lap", "tulajdonilap", "ingatlan allapota", "tehermentes", "onkormanyzati"]),
        # product_choice
        ("product_choice", ["milyen celra", "kivalasztott termek", "termek valasztas", "igenyelt termek"]),
        # bank_info
        ("bank_info", ["bank", "kondicio", "szerzodes", "tajekoztato", "hitelintezeti", "kamatozasi"]),
        # consent – legutolsó, leggyakoribb
        ("consent", ["hozzajarul", "elfogad", "tudomasul", "kijelentem", "nyilatkozom", "igazolom", "igenlom"]),
    ]

    def __init__(
        self,
        config_path: Optional[Path] = None,
        prompt_path: Optional[Path] = None,
        api_key: Optional[str] = None,
    ):
        """
        Args:
            config_path: legal_defaults.json útvonala (default: src/mapping/).
            prompt_path: legal_classifier_prompt.txt útvonala.
            api_key: DeepSeek API kulcs. Ha None, DEEPSEEK_API_KEY env-ből.
        """
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.prompt_path = Path(prompt_path) if prompt_path else DEFAULT_PROMPT_PATH
        self.config = self._load_config()

        if api_key is None:
            import os
            api_key = os.getenv("DEEPSEEK_API_KEY", "")

        self._api_key = api_key or ""
        if not self._api_key:
            logger.info(
                "DEEPSEEK_API_KEY nincs beállítva – LegalClassifier rule-based "
                "módot használ."
            )

    # ------------------------------------------------------------------
    # Betöltés
    # ------------------------------------------------------------------

    def _load_config(self) -> dict:
        if not self.config_path.exists():
            logger.warning(
                "legal_defaults.json nem található: %s – üres configtal dolgozom.",
                self.config_path,
            )
            return {"categories": {}, "conditional_rules": [], "overrides": {}}
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_prompt_template(self) -> str:
        if not self.prompt_path.exists():
            logger.warning(
                "legal_classifier_prompt.txt nem található: %s – beépített prompt.",
                self.prompt_path,
            )
            return (
                "Kategorizáld a következő banki checkbox mezőket.\n\n"
                "Kategóriák: consent, data_sharing, bank_info, decline, "
                "property_state, conditional, product_choice, unknown\n\n"
                "Mezők:\n{pdf_field_list}\n\n"
                "Válasz: JSON tömb: "
                '[{{"f": "mező_név", "c": "kategória"}}, ...]'
            )
        with open(self.prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    # ------------------------------------------------------------------
    # Nyilvános API
    # ------------------------------------------------------------------

    def classify_batch(self, fields: list[dict]) -> dict[str, str]:
        """
        Leképezetlen checkbox-ok kategorizálása.

        Args:
            fields: lista dict-ekből, pl.:
                [{"pdf_field_name": "...", "label": "...", "page_number": 1}, ...]

        Returns:
            {"pdf_field_name": "category", ...}

        AI nélkül (nincs kliens) automatikusan classify_rule_based-re vált.
        """
        if not fields:
            return {}

        if not self._api_key:
            logger.info(
                "LegalClassifier: rule-based fallback (%d mező)", len(fields)
            )
            return self.classify_rule_based(fields)

        template = self._load_prompt_template()
        result: dict[str, str] = {}

        # Batches
        for start in range(0, len(fields), BATCH_SIZE):
            batch = fields[start : start + BATCH_SIZE]
            batch_result = self._ai_classify_one_batch(batch, template)
            result.update(batch_result)
            # Hiányzó mezőkre rule-based fallback, hogy ne maradjon ki senki.
            for f in batch:
                name = f.get("pdf_field_name") or f.get("name")
                if name and name not in result:
                    result[name] = self._rule_based_single(f)
            if start + BATCH_SIZE < len(fields):
                time.sleep(1)  # rate limit

        return result

    def apply_defaults(
        self,
        classified: dict[str, str],
        deal: DealData,
    ) -> dict[str, bool]:
        """
        Kategóriák → konkrét PDF mező értékek (true/false).

        A conditional_rules a DealData.products alapján dönt.
        A manual kategóriák (product_choice, unknown) kimaradnak.

        Returns:
            {"pdf_field_name": True/False, ...}
        """
        categories = self.config.get("categories", {})
        overrides = self.config.get("overrides", {}) or {}
        rules = self.config.get("conditional_rules", []) or []

        # products előkészítése (normalized halmaz a rule matching-hez)
        products_norm = {_normalize(p) for p in (deal.products or [])}

        out: dict[str, bool] = {}
        for pdf_name, category in classified.items():
            # 1. Explicit override él elsőbbséggel.
            if pdf_name in overrides and overrides[pdf_name] is not None:
                out[pdf_name] = bool(overrides[pdf_name])
                continue

            cat = categories.get(category)
            if cat is None:
                # Ismeretlen kategória → kimarad (emberi review).
                continue

            apply_when = cat.get("apply_when", "always")

            if apply_when == "manual":
                # product_choice / unknown → ember dönt.
                continue
            elif apply_when == "conditional":
                value = self._resolve_conditional(
                    category, products_norm, rules
                )
                if value is None:
                    # Nincs egyező conditional szabály → default_value ha van,
                    # különben kimarad.
                    default_val = cat.get("default_value")
                    if default_val is None:
                        continue
                    out[pdf_name] = bool(default_val)
                else:
                    out[pdf_name] = value
            else:  # always
                default_val = cat.get("default_value")
                if default_val is None:
                    continue
                out[pdf_name] = bool(default_val)

        return out

    def classify_rule_based(self, fields: list[dict]) -> dict[str, str]:
        """Rule-based fallback – kulcsszó alapú kategorizálás AI nélkül."""
        out: dict[str, str] = {}
        for f in fields:
            out[f.get("pdf_field_name") or f.get("name", "")] = self._rule_based_single(f)
        return out

    # ------------------------------------------------------------------
    # AI hívás (privát)
    # ------------------------------------------------------------------

    def _ai_classify_one_batch(
        self, batch: list[dict], template: str
    ) -> dict[str, str]:
        """Egyetlen DeepSeek hívás egy batch mezőre."""
        # Kompakt lista: név | label | oldal
        lines = []
        for f in batch:
            name = f.get("pdf_field_name") or f.get("name", "")
            label = (f.get("label") or "").replace("\n", " ").strip()[:120]
            page = f.get("page_number") or f.get("page") or "?"
            lines.append(f"{name} | {label} | p{page}")
        fields_text = "\n".join(lines)

        prompt = template.replace("{pdf_field_list}", fields_text)

        try:
            import requests
            response = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.0,
                },
                timeout=60,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.error("LegalClassifier API hiba: %s", str(exc)[:160])
            return {}

        # Válasz kinyerése
        try:
            text = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("LegalClassifier: érvénytelen AI válasz: %s", str(exc)[:120])
            return {}

        if not text:
            logger.warning("LegalClassifier: üres AI válasz")
            return {}

        return self._parse_ai_response(text)

    def _parse_ai_response(self, text: str) -> dict[str, str]:
        """AI válasz (JSON tömb) parse-olása."""
        json_str = self._extract_json(text)
        try:
            items = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.warning("LegalClassifier JSON hiba: %s", str(exc)[:120])
            return {}

        valid_categories = {
            "consent", "data_sharing", "bank_info", "decline",
            "property_state", "product_choice", "conditional", "unknown",
        }
        out: dict[str, str] = {}
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = item.get("f") or item.get("name") or item.get("field")
                cat = (item.get("c") or item.get("category") or "unknown").strip().lower()
                if name and cat in valid_categories:
                    out[name] = cat
        elif isinstance(items, dict):
            # Ha dict-et kapunk {név: kategória} formában, azt is elfogadjuk.
            for name, cat in items.items():
                cat = str(cat).strip().lower()
                if cat in valid_categories:
                    out[name] = cat
        return out

    @staticmethod
    def _extract_json(text: str) -> str:
        """JSON blokk kinyerése a válaszból (```json ... ``` vagy sima JSON)."""
        m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        m = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
        if m and (m.group(1).strip().startswith("[") or m.group(1).strip().startswith("{")):
            return m.group(1).strip()
        # Első [ ... ] vagy { ... }
        if "[" in text:
            start = text.index("[")
            end = text.rindex("]") + 1
            return text[start:end]
        if "{" in text:
            start = text.index("{")
            end = text.rindex("}") + 1
            return text[start:end]
        raise ValueError("Nem található JSON az AI válaszban")

    # ------------------------------------------------------------------
    # Rule-based helpers
    # ------------------------------------------------------------------

    def _rule_based_single(self, f: dict) -> str:
        """Egyetlen mező kategóriája kulcsszavak alapján."""
        name = f.get("pdf_field_name") or f.get("name", "") or ""
        label = f.get("label", "") or ""
        haystack = _normalize(f"{name} {label}")
        if not haystack:
            return "unknown"
        for category, keywords in self.RULE_TABLE:
            for kw in keywords:
                if kw in haystack:
                    return category
        return "unknown"

    def _resolve_conditional(
        self,
        category: str,
        products_norm: set[str],
        rules: list[dict],
    ) -> Optional[bool]:
        """
        conditional_rules alapján eldönti egy feltételes kategória értékét.

        Args:
            category: a mező kategóriája (általában "conditional").
            products_norm: normalized DealData.products halmaz.
            rules: a legal_defaults.json conditional_rules listája.

        Returns:
            True/False vagy None (ha egyetlen szabály sem illeszkedik).
        """
        for rule in rules:
            if rule.get("category") != category:
                continue
            cond = rule.get("if", {}) or {}
            key = "products_contains"
            if key in cond:
                wanted = _normalize(str(cond[key]))
                if any(wanted in p for p in products_norm):
                    val = rule.get("default_value")
                    return bool(val) if val is not None else None
        return None


# ----------------------------------------------------------------------
# Kényelmi függvények
# ----------------------------------------------------------------------

def extract_unmapped_checkboxes(mapping) -> list[dict]:
    """
    Kinyeri a canonical_field nélküli checkbox mezőket egy MappingConfig-ból.

    Args:
        mapping: src.ai.field_recognizer.MappingConfig

    Returns:
        [{"pdf_field_name", "label", "page_number"}, ...]
    """
    out = []
    for f in getattr(mapping, "fields", []) or []:
        if f.canonical_field:
            continue
        if str(getattr(f, "field_type", "")).lower().split(".")[-1] != "checkbox":
            continue
        out.append({
            "pdf_field_name": f.pdf_field_name,
            "label": f.label or "",
            "page_number": getattr(f, "page_number", 1) or 1,
        })
    return out


def fill_legal_declarations_on_pdf(pdf_path: Path, values: dict[str, bool]) -> int:
    """
    Megnyitja a PDF-et (pikepdf) és bepipálja / kikapcsolja a megadott
    AcroForm checkbox mezőket. Visszaadja a módosított mezők számát.

    Csak /Btn típusú mezőket érint. A többi mezőhöz nem nyúl.
    """
    import pikepdf

    if not values:
        return 0

    modified = 0
    with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
        if "/AcroForm" not in pdf.Root:
            return 0
        acroform = pdf.Root["/AcroForm"]
        if "/Fields" not in acroform:
            return 0

        def _walk(fields):
            nonlocal modified
            for field_ref in fields:
                try:
                    f = field_ref
                    if "/Kids" in f:
                        _walk(f["/Kids"])
                        continue
                    name = str(f.get("/T", ""))
                    if name not in values:
                        continue
                    if str(f.get("/FT", "")) != "/Btn":
                        continue
                    is_checked = bool(values[name])
                    f[pikepdf.Name("/V")] = (
                        pikepdf.Name("/Yes") if is_checked else pikepdf.Name("/Off")
                    )
                    if "/AP" in f:
                        del f["/AP"]
                    modified += 1
                except Exception as exc:
                    logger.debug("Checkbox módosítási hiba: %s", exc)

        _walk(acroform["/Fields"])

        if modified:
            pdf.save(str(pdf_path))

    return modified
