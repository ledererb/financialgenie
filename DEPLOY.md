# FinancialGenie – Szerver Telepítési Útmutató

Ez a dokumentum egy AI-agent (vagy DevOps mérnök) számára készült, aki a FinancialGenie alkalmazást egy szerverre telepíti és elindítja.

---

## 1. A projekt rövid leírása

A FinancialGenie egy banki PDF nyomtatvány-kitöltő rendszer, amely Salesforce-ból kéri le az ügyletadatokat. Két részből áll:

- **Backend**: Python FastAPI szerver (port `8765`)
- **Frontend**: React + Vite alkalmazás (fejlesztésben port `5180`, prodban statikus fájlokból szolgálható)

A frontend a `/api` path-on proxy-zza a kéréseket a backendhez.

---

## 2. Előfeltételek

| Szoftver | Minimum verzió | Ellenőrzés |
|---|---|---|
| Python | 3.11+ | `python3 --version` |
| pip | 23+ | `pip3 --version` |
| Node.js | 20+ | `node --version` |
| npm | 9+ | `npm --version` |
| git | 2.30+ | `git --version` |

---

## 3. Forráskód letöltése

```bash
git clone https://github.com/ledererb/rufus-pb.git financialgenie
cd financialgenie
git checkout feature/mapping-editor
```

---

## 4. Környezeti változók konfigurálása

Hozz létre egy `config/.env` fájlt. **Ez a fájl NEM szerepel a git repo-ban** (`.gitignore`-ban van).

```bash
cat > config/.env << 'EOF'
# Salesforce sandbox credentials
SF_USERNAME=your_sf_username@example.com
SF_PASSWORD=your_sf_password
SF_SECURITY_TOKEN=your_sf_security_token
SF_DOMAIN=test

# Anthropic API (opcionális – csak az AI mezőfelismeréshez kell)
ANTHROPIC_API_KEY=sk-ant-...

# Logging
LOG_LEVEL=INFO
EOF
```

> **FONTOS:** A `SF_DOMAIN` értéke `test` a sandbox-hoz, `login` az éles Salesforce-hoz.

---

## 5. Backend telepítése

```bash
# Python virtuális környezet létrehozása
python3 -m venv venv
source venv/bin/activate

# Függőségek telepítése (mindkét requirements.txt kell)
pip install -r requirements.txt
pip install -r backend/requirements.txt

# Telepítés ellenőrzése
python -c "import fastapi, pikepdf, fitz, simple_salesforce; print('OK')"
```

---

## 6. Frontend build

```bash
cd frontend
npm install
npm run build
cd ..
```

A build eredménye a `frontend/dist/` mappában lesz. Ez statikus HTML/JS/CSS.

---

## 7. Indítás – Fejlesztési mód

Ha csak tesztelni akarod:

```bash
# Backend indítása (egy terminálban)
source venv/bin/activate
python3 backend/server.py
# → http://localhost:8765

# Frontend indítása (másik terminálban)
cd frontend
npm run dev
# → http://localhost:5180  (proxy-zza a /api kéréseket a 8765-re)
```

Vagy egyben:
```bash
bash START.sh
```

---

## 8. Indítás – Produkciós mód

### 8.1 Backend indítása

A backend egy FastAPI alkalmazás, amit uvicorn szolgál ki. Produkciós indítás:

```bash
source venv/bin/activate
cd backend
uvicorn server:app \
  --host 0.0.0.0 \
  --port 8765 \
  --workers 2 \
  --log-level info \
  --app-dir /path/to/financialgenie/backend
```

Fontos `--host 0.0.0.0`, hogy kívülről is elérhető legyen, ne csak localhost-ról.

### 8.2 Frontend kiszolgálása

A frontend build-elt (`frontend/dist/`) statikus fájljait egy webszerverrel kell kiszolgálni (nginx, caddy, stb.), és a `/api` kéréseket a backend-re kell proxy-zni.

**Nginx példa konfiguráció:**

```nginx
server {
    listen 80;
    server_name financialgenie.example.com;

    # Frontend statikus fájlok
    root /path/to/financialgenie/frontend/dist;
    index index.html;

    # SPA routing – minden nem-fájl kérés az index.html-re megy
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API kérések proxy-zása a backend-re
    location /api/ {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Nagy PDF-ek miatt
        client_max_body_size 50M;
        proxy_read_timeout 120s;
    }
}
```

### 8.3 Systemd service (opcionális)

Ha systemd-vel akarod menedzselni a backendet:

```ini
# /etc/systemd/system/financialgenie.service
[Unit]
Description=FinancialGenie Backend
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/financialgenie
ExecStart=/path/to/financialgenie/venv/bin/uvicorn server:app --host 0.0.0.0 --port 8765 --workers 2 --app-dir /path/to/financialgenie/backend
EnvironmentFile=/path/to/financialgenie/config/.env
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable financialgenie
sudo systemctl start financialgenie
sudo systemctl status financialgenie
```

---

## 9. Mappastruktúra – mi hol van

```
financialgenie/
├── backend/
│   ├── server.py            ← FastAPI belépési pont
│   ├── config.py            ← PDF/mapping útvonalak
│   ├── pdf_service.py       ← PDF feldolgozás
│   ├── mapping_service.py   ← Mapping CRUD
│   └── requirements.txt     ← Backend-specifikus függőségek
├── frontend/
│   ├── src/                 ← React forráskód
│   ├── dist/                ← Build output (npm run build után)
│   ├── package.json
│   └── vite.config.ts
├── src/
│   ├── main.py              ← Kitöltő pipeline
│   ├── integrations/        ← Salesforce kliens
│   ├── engine/              ← PDF kitöltő motor
│   ├── mapping/             ← Mapping JSON-ok
│   └── models/              ← Adatmodellek
├── config/
│   ├── settings.py          ← Konfiguráció betöltő
│   └── .env                 ← Titkos kulcsok (NEM git-ben!)
├── otp/                     ← OTP bank PDF sablonok
├── samples/                 ← Feltöltött PDF-ek
├── output/                  ← Kitöltött PDF-ek (generált)
├── requirements.txt         ← Fő Python függőségek
└── START.sh                 ← Dev indítószkript
```

---

## 10. Fontos port-ok és URL-ek

| Szolgáltatás | Port | URL |
|---|---|---|
| Backend (FastAPI) | 8765 | `http://localhost:8765` |
| Frontend (dev mód) | 5180 | `http://localhost:5180` |
| Frontend (prod) | 80/443 | nginx/caddy mögött |

---

## 11. Hibaelhárítás

### Backend nem indul
```bash
# Port foglalt?
lsof -i:8765
# Ha igen, öld meg:
kill -9 $(lsof -ti:8765)
```

### Salesforce autentikáció sikertelen
- Ellenőrizd a `config/.env` fájlban a credentials-öket
- `SF_DOMAIN=test` a sandbox-hoz, `SF_DOMAIN=login` az éleshez
- Ha "security token" hiba van: a Salesforce felhasználó beállításaiban kell újragenerálni

### Frontend build hiba
```bash
cd frontend
rm -rf node_modules
npm install
npm run build
```

### PDF-ek nem töltődnek be
- Ellenőrizd, hogy az `otp/` és `samples/` mappák léteznek és olvashatók
- A backend a projekt gyökérhez képest relatív útvonalakat használ

---

## 12. Ellenőrző lista telepítés után

- [ ] `config/.env` kitöltve a Salesforce credentials-ökkel
- [ ] `python3 backend/server.py` hiba nélkül elindul
- [ ] `curl http://localhost:8765/api/pdfs` válaszol
- [ ] `curl http://localhost:8765/api/sf/deals` visszaad deal-eket (SF kapcsolat működik)
- [ ] Frontend build sikeres (`frontend/dist/index.html` létezik)
- [ ] Nginx/proxy konfiguráció működik (`/api` kérések átmennek)
- [ ] Böngészőben megnyílik a Mapping Stúdió
- [ ] PDF feltöltés működik
- [ ] Salesforce deal kiválasztás működik a dropdown-ban
