# WFTOOL: app and scheduler in one process (waitress), data in /data, key file in /keys.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WFTOOL_DATA=/data \
    WFTOOL_KEY_FILE=/keys/wftool.key

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin wftool \
 && mkdir -p /data /keys \
 && chown wftool:wftool /data /keys

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wftool ./wftool
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh

VOLUME ["/data", "/keys"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/login', timeout=4)" || exit 1

# The entrypoint gives /data and /keys to the wftool user, then starts the app as that user (never as root).
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "wftool", "run", "--host", "0.0.0.0", "--port", "8080"]
