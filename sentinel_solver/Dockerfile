FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt ./
# Full Chromium only. The headless shell does not pass the Cloudflare check.
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps --no-shell chromium \
    && rm -rf /var/lib/apt/lists/*

COPY app.py vm.js LICENSE ./

EXPOSE 8080
CMD ["python", "app.py"]
