# Run every WFTOOL test in Docker (fresh data each run). Results: .\test-results\report.html
Set-Location (Join-Path $PSScriptRoot "..")
docker compose -f docker-compose.test.yml up --build --renew-anon-volumes --abort-on-container-exit --exit-code-from tests
$status = $LASTEXITCODE
docker compose -f docker-compose.test.yml down --volumes --remove-orphans | Out-Null
Write-Host "Report: $(Join-Path (Get-Location) 'test-results\report.html')"
exit $status
