# AI Endurance Coach — image de l'application (amd64 et arm64, ex. Oracle Ampere)

# 1) interface React : construite ici, seul le résultat (HTML/JS/CSS statiques) passe dans l'image finale
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
# le build lit ../VERSION et la feuille de style partagée (coach/web/static/styles.css + polices)
COPY VERSION /app/VERSION
COPY coach/web/static/styles.css /app/coach/web/static/styles.css
COPY coach/web/static/fonts /app/coach/web/static/fonts
COPY frontend/ ./
RUN npm run build

# 2) application Python
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    COACH_HOME=/data COACH_TZ=Europe/Paris COACH_TRUSTED_PROXIES=* \
    COACH_STT_DIR=/models HF_HOME=/models/hf
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
# dictée (v0.21) : si l'installation échoue, l'app fonctionne sans (le bouton micro utilise le navigateur)
COPY requirements-stt.txt .
RUN pip install -r requirements-stt.txt || echo "dictée serveur non installée"
COPY . .
COPY --from=frontend /app/coach/web/static/app /app/coach/web/static/app
RUN useradd --create-home --uid 1000 coach && mkdir -p /data /models && chown coach:coach /data /models
USER coach
VOLUME ["/data", "/models"]
EXPOSE 8000
# --claude seulement si une clé est fournie
CMD ["sh", "-c", "exec python -m coach.web serve --host 0.0.0.0 --port 8000 ${ANTHROPIC_API_KEY:+--claude}"]
