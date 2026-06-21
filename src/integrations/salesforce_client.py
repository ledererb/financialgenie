"""
FinancialGenie – Salesforce mock kliens

Valódi Salesforce sandbox nélkül szimulálja az API műveleteket:
- Ügylet- és szereplőadatok lekérése (olvasás)
- PDF csatolás és stage-váltás (visszaírás)

A PoC-ban dummy JSON adatokból dolgozik.
Éles módban a simple-salesforce csomag veszi át.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SalesforceAttachment:
    """Salesforce-ba feltöltött csatolmány."""
    filename: str
    content_type: str
    deal_id: str
    uploaded_at: datetime
    file_path: Path


@dataclass
class MockSalesforceState:
    """A mock Salesforce belső állapota."""
    deals: dict = field(default_factory=dict)         # deal_id → deal JSON
    stages: dict = field(default_factory=dict)         # deal_id → stage
    attachments: list = field(default_factory=list)     # SalesforceAttachment lista


class SalesforceClient:
    """
    Salesforce integráció – olvasás és visszaírás.
    
    Mock módban (sandbox nélkül) a samples/dummy_data/ könyvtárból 
    olvassa az adatokat. Éles módban a simple-salesforce csomag 
    OAuth 2.0-n keresztül csatlakozik.
    """

    def __init__(
        self,
        username: str = "",
        password: str = "",
        security_token: str = "",
        domain: str = "test",
        mock_mode: bool = True,
        mock_data_dir: Path = None,
    ):
        self._mock_mode = mock_mode
        self._state = MockSalesforceState()
        
        if mock_mode:
            self._mock_data_dir = mock_data_dir or Path("samples/dummy_data")
            self._load_mock_data()
            logger.info("Salesforce mock mód aktív")
        else:
            try:
                from simple_salesforce import Salesforce
                self._sf = Salesforce(
                    username=username,
                    password=password,
                    security_token=security_token,
                    domain=domain,
                )
                logger.info(f"Salesforce csatlakozva: {domain}")
            except Exception as e:
                logger.error(f"Salesforce csatlakozási hiba: {e}")
                raise

    def _load_mock_data(self):
        """Mock adatok betöltése a JSON fájlokból."""
        if not self._mock_data_dir.exists():
            logger.warning(f"Mock adat könyvtár nem található: {self._mock_data_dir}")
            return

        for json_file in self._mock_data_dir.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    deal_data = json.load(f)
                deal_id = deal_data.get("deal_id", json_file.stem)
                self._state.deals[deal_id] = deal_data
                self._state.stages[deal_id] = deal_data.get(
                    "salesforce_stage", "Dokumentáció készítés"
                )
                logger.debug(f"Mock ügylet betöltve: {deal_id} ({json_file.name})")
            except Exception as e:
                logger.warning(f"Mock adat betöltési hiba ({json_file}): {e}")

        logger.info(f"{len(self._state.deals)} mock ügylet betöltve")

    def get_deal(self, deal_id: str) -> Optional[dict]:
        """
        Ügylet lekérése azonosító alapján.
        
        Returns:
            Ügylet adatok dict-ként, vagy None ha nem található.
        """
        if self._mock_mode:
            deal = self._state.deals.get(deal_id)
            if deal:
                logger.info(f"Ügylet lekérve (mock): {deal_id}")
            else:
                # Ha nem pontos ID, próbáljuk az első találatot
                for did, ddata in self._state.deals.items():
                    logger.info(f"Ügylet lekérve (mock, első találat): {did}")
                    return ddata
                logger.warning(f"Ügylet nem található (mock): {deal_id}")
            return deal
        else:
            try:
                result = self._sf.Opportunity.get(deal_id)
                logger.info(f"Ügylet lekérve (SF): {deal_id}")
                return result
            except Exception as e:
                logger.error(f"Ügylet lekérési hiba: {e}")
                return None

    def list_deals(self) -> list[dict]:
        """Elérhető ügyletek listázása."""
        if self._mock_mode:
            return [
                {"deal_id": did, "stage": self._state.stages.get(did)}
                for did in self._state.deals
            ]
        else:
            query = "SELECT Id, Name, StageName FROM Opportunity LIMIT 50"
            result = self._sf.query(query)
            return result.get("records", [])

    def get_deal_stage(self, deal_id: str) -> Optional[str]:
        """Ügylet aktuális stage-ének lekérése."""
        if self._mock_mode:
            return self._state.stages.get(deal_id)
        else:
            deal = self.get_deal(deal_id)
            return deal.get("StageName") if deal else None

    def update_stage(self, deal_id: str, new_stage: str) -> bool:
        """
        Ügylet stage-váltása.
        
        A kitöltés után a "Dokumentáció kész" stage-be lép,
        jelezve, hogy emberi review-ra vár.
        """
        if self._mock_mode:
            old_stage = self._state.stages.get(deal_id, "ismeretlen")
            self._state.stages[deal_id] = new_stage
            logger.info(f"Stage váltás (mock): {deal_id}: {old_stage} → {new_stage}")
            return True
        else:
            try:
                self._sf.Opportunity.update(deal_id, {"StageName": new_stage})
                logger.info(f"Stage váltás (SF): {deal_id} → {new_stage}")
                return True
            except Exception as e:
                logger.error(f"Stage váltás hiba: {e}")
                return False

    def attach_pdf(self, deal_id: str, pdf_path: Path, filename: str = None) -> bool:
        """
        PDF csatolása az ügylethez.
        
        A kész, kitöltött nyomtatvány visszakerül a Salesforce-ba.
        """
        if filename is None:
            filename = pdf_path.name

        if self._mock_mode:
            attachment = SalesforceAttachment(
                filename=filename,
                content_type="application/pdf",
                deal_id=deal_id,
                uploaded_at=datetime.now(),
                file_path=pdf_path,
            )
            self._state.attachments.append(attachment)
            logger.info(f"PDF csatolva (mock): {filename} → {deal_id}")
            return True
        else:
            try:
                with open(pdf_path, "rb") as f:
                    pdf_content = f.read()

                import base64
                encoded = base64.b64encode(pdf_content).decode("utf-8")

                # ContentVersion létrehozása (Salesforce Files)
                self._sf.ContentVersion.create({
                    "Title": filename,
                    "PathOnClient": filename,
                    "VersionData": encoded,
                    "FirstPublishLocationId": deal_id,
                })
                logger.info(f"PDF csatolva (SF): {filename} → {deal_id}")
                return True
            except Exception as e:
                logger.error(f"PDF csatolási hiba: {e}")
                return False

    def get_attachments(self, deal_id: str) -> list[dict]:
        """Ügylethez csatolt fájlok listázása."""
        if self._mock_mode:
            return [
                {
                    "filename": a.filename,
                    "uploaded_at": a.uploaded_at.isoformat(),
                    "file_path": str(a.file_path),
                }
                for a in self._state.attachments
                if a.deal_id == deal_id
            ]
        else:
            query = (
                f"SELECT ContentDocument.Title, ContentDocument.CreatedDate "
                f"FROM ContentDocumentLink "
                f"WHERE LinkedEntityId = '{deal_id}'"
            )
            result = self._sf.query(query)
            return result.get("records", [])
