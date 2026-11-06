# Test runner: Python, pytest and Playwright with Chromium (the version matches requirements-test.txt).
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
WORKDIR /suite
COPY requirements.txt requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-test.txt
COPY pyproject.toml ./
COPY wftool ./wftool
COPY tests ./tests
COPY examples ./examples
COPY e2e ./e2e
CMD ["python", "-m", "e2e.run"]
