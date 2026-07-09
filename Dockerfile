# ============================================
# FinancialGenie – Backend
# ============================================
FROM python:3.12-slim AS backend

# Rendszer-függőségek (PyMuPDF, pikepdf számára)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libmupdf-dev \
    libfreetype6-dev \
    libjpeg62-turbo-dev \
    libopenjp2-7-dev \
    libharfbuzz-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python függőségek telepítése (cache-elés miatt külön lépés)
COPY requirements.txt ./requirements.txt
COPY backend/requirements.txt ./requirements-backend.txt
RUN pip install --no-cache-dir \
    -r requirements.txt \
    -r requirements-backend.txt \
    && rm -f requirements.txt requirements-backend.txt

# Alkalmazás kód másolása
COPY src/ ./src/
COPY backend/ ./backend/
COPY config/settings.py ./config/settings.py

# Mappák létrehozása
RUN mkdir -p output samples otp

# Port
EXPOSE 8765

# Indítás
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8765", "--app-dir", "/app/backend"]

# ============================================
# FinancialGenie – Frontend build
# ============================================
FROM node:20-slim AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --ignore-scripts || npm install
COPY frontend/ ./
RUN npm run build

# ============================================
# FinancialGenie – Nginx (frontend + API proxy)
# ============================================
FROM nginx:alpine AS frontend

# Frontend build eredmény másolása
COPY --from=frontend-build /app/frontend/dist /usr/share/nginx/html

# Nginx konfiguráció
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
