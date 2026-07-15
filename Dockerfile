FROM python:3.11

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xvfb x11vnc \
    && curl --fail --location --insecure \
        "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt" \
        --output /usr/local/share/ca-certificates/russian_trusted_root_ca.crt \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Playwright browsers + OS deps
RUN playwright install --with-deps chromium

COPY . .

RUN chmod +x docker/entrypoint.sh

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

ENTRYPOINT ["docker/entrypoint.sh"]

CMD ["python", "main.py"]
