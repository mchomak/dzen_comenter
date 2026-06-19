FROM python:3.11

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers + OS deps
RUN playwright install --with-deps chromium

COPY . .

# Финальный CMD/entrypoint задаётся в Волне 2.
