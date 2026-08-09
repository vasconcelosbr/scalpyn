param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('seed-start', 'snapshot', 'resume', 'origin-start', 'origin-snapshot', 'full-canary')]
    [string]$Action,
    [string]$RunId
)

$ErrorActionPreference = 'Stop'
$token = $env:DIAGNOSTICS_BEARER_TOKEN
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'DIAGNOSTICS_BEARER_TOKEN_MISSING'
}
if (($Action -in @('snapshot', 'resume')) -and [string]::IsNullOrWhiteSpace($RunId)) {
    throw 'RUN_ID_REQUIRED'
}

$uri = 'https://scalpyn-langgraph-staging-api-systemic-ai-staging-20260807.up.railway.app/api/ai/graphs/staging-crash-resume'
$body = @{ action = $Action }
if ($Action -eq 'full-canary') {
    $uri = 'https://scalpyn-langgraph-staging-api-systemic-ai-staging-20260807.up.railway.app/api/ai/graphs/staging-canary'
    $body = @{}
} elseif ($RunId) {
    $body.run_id = $RunId
}
$response = Invoke-RestMethod `
    -Method Post `
    -Uri $uri `
    -Headers @{ Authorization = "Bearer $token" } `
    -ContentType 'application/json' `
    -Body ($body | ConvertTo-Json -Compress) `
    -TimeoutSec 60
$response | ConvertTo-Json -Depth 20 -Compress
