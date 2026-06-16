FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG CACHEBUST=1
COPY . .

# Code version (git short SHA) baked at build for version reporting.
# Pass --build-arg GIT_SHA=$(git rev-parse --short HEAD); defaults to "dev".
ARG GIT_SHA=dev
ENV WORKER_CODE_VERSION=$GIT_SHA

CMD ["python", "-m", "worker.main"]
