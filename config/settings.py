"""
FinancialGenie – Konfigurációkezelés

Környezeti változók betöltése és projekt-szintű beállítások.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Projekt gyökér
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"
SAMPLES_DIR = PROJECT_ROOT / "samples"
MAPPINGS_DIR = PROJECT_ROOT / "src" / "mapping"

# .env betöltése
env_path = CONFIG_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)

# API kulcsok
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Salesforce
SF_USERNAME = os.getenv("SF_USERNAME", "")
SF_PASSWORD = os.getenv("SF_PASSWORD", "")
SF_SECURITY_TOKEN = os.getenv("SF_SECURITY_TOKEN", "")
SF_DOMAIN = os.getenv("SF_DOMAIN", "test")

# Általános
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Könyvtárak létrehozása ha nem léteznek
OUTPUT_DIR.mkdir(exist_ok=True)
SAMPLES_DIR.mkdir(exist_ok=True)
