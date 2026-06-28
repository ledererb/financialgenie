"""
FinancialGenie – Pipeline eredmény-típusok

Ezek a dataclass-ok a 4-pass pipeline és az orchestrátor közös
"eredmény nyelvét" adják. Minden pass egy `PassResult`-ot ad vissza,
az orchestrátor pedig egy `PipelineResult`-ot, amely tartalmazza az
összes pass eredményét + az általános lefedettséget.

Ezek a típusok függetlenek a konkrét kitöltőmotortól (AcroForm /
overlay) és a forrástól (Salesforce / mock) – tiszta adatstruktúrák.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PassResult:
    """
    Egyetlen pass futásának eredménye.

    Attributes:
        name: A pass neve ("data" | "mapping" | "fill" | "writeback").
        success: Sikeres volt-e a pass (nem dobott kivételt és a
            pass-specifikus sikerfeltétel teljesült).
        metrics: Pass-specifikus metrikák (pl. coverage, mapped count,
            output_path). Szabadon alakítható, de a `coverage` és a
            `total`/`filled` kulcsokat érdemes tartalmaznia ahol van
            értelme.
        issues: Emberi olvasásra szánt hiba/figyelmeztető üzenetek listája.
    """
    name: str
    success: bool
    metrics: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """Convenience accessor: 0.0-1.0 lefedettség (ha van)."""
        return float(self.metrics.get("coverage", 0.0))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "success": self.success,
            "metrics": self.metrics,
            "issues": self.issues,
        }


@dataclass
class PipelineResult:
    """
    A teljes 4-pass pipeline eredménye.

    Attributes:
        deal_id: A Salesforce ügylet azonosító.
        success: Összesített siker (mind a 4 pass sikeres volt-e).
        output_path: A generált PDF útvonala (ha sikeres a fill pass).
        passes: Név → PassResult mapping ("data" | "mapping" | "fill" | "writeback").
        issues: Összes hiba/figyelmeztetés az összes pass-ból.
        overall_coverage: A mapping pass lefedettsége (a legfontosabb metrika).
        timestamp: ISO-formátumú időbélyeg.
    """
    deal_id: str
    success: bool
    output_path: Optional[Path] = None
    passes: dict[str, PassResult] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    overall_coverage: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "deal_id": self.deal_id,
            "success": self.success,
            "output_path": str(self.output_path) if self.output_path else None,
            "passes": {k: v.to_dict() for k, v in self.passes.items()},
            "issues": self.issues,
            "overall_coverage": self.overall_coverage,
            "timestamp": self.timestamp,
        }

    @property
    def is_skipped(self) -> bool:
        """A mapping pass kihagyta-e a futást (pl. nincs mapping)."""
        mp = self.passes.get("mapping")
        return bool(mp and mp.metrics.get("skipped"))


def merge_issues(passes: dict[str, PassResult]) -> list[str]:
    """Összefogja az összes pass issue-listáját egyetlen listába."""
    out: list[str] = []
    for p in passes.values():
        out.extend(p.issues)
    return out
