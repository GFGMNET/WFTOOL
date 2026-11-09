# Test runner: Python, pytest, Playwright and Debian's Chromium.
# Downloads only from Docker Hub, the Debian package servers and PyPI (no Microsoft registry or CDN).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    E2E_CHROME=/usr/bin/chromium

RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-liberation fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /suite
COPY requirements.txt requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-test.txt
COPY pyproject.toml ./
COPY wftool ./wftool
COPY tests ./tests
COPY examples ./examples
COPY e2e ./e2e
CMD ["python", "-m", "e2e.run"]
