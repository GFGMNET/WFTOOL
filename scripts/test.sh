#!/bin/sh
# Run every WFTOOL test in Docker (fresh data each run). Results: ./test-results/report.html
cd "$(dirname "$0")/.." || exit 1
docker compose -f docker-compose.test.yml up --build --renew-anon-volumes --abort-on-container-exit --exit-code-from tests
status=$?
docker compose -f docker-compose.test.yml down --volumes --remove-orphans >/dev/null 2>&1
echo "Report: $(pwd)/test-results/report.html"
exit $status
