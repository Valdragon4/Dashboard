FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    BUN_INSTALL=/root/.bun \
    PATH=/root/.bun/bin:$PATH \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl unzip chromium && rm -rf /var/lib/apt/lists/*

# Installer Bun pour exécuter le bridge Trade Republic en mode script.
RUN curl -fsSL https://bun.sh/install | bash

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Installer les navigateurs Playwright nécessaires pour BoursoBank et Hello Bank
RUN playwright install chromium
RUN playwright install-deps chromium

COPY . /app

EXPOSE 8000


