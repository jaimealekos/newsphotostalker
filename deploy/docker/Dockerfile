# Imagen Docker de newsphotostalker para un servidor.
# Base de Playwright (trae Xvfb y las dependencias del navegador) + Google Chrome
# real (mejor huella para Reuters/DataDome que el Chromium de Playwright).
#
# Construir desde la RAÍZ del repo:
#   docker build -t newsphotostalker -f deploy/docker/Dockerfile .
# O con docker compose (ver docker-compose.yml en la raíz).
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

# Google Chrome estable (para Reuters). En config.local.yaml pon:
#   playwright.executable_path: /usr/bin/google-chrome
RUN apt-get update \
 && apt-get install -y --no-install-recommends wget gnupg ca-certificates \
 && wget -qO- https://dl.google.com/linux/linux_signing_key.pub \
      | gpg --dearmor -o /usr/share/keyrings/google.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
      > /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt \
 || pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
# Reuters corre HEADED: with-xvfb.sh lanza Xvfb y ejecuta uvicorn bajo ese display.
# Correr el contenedor con --init (docker run --init / init: true en compose).
CMD ["sh","deploy/docker/with-xvfb.sh","uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
