[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$ExpectedRemoteRef = "origin/main",
    [string]$ManifestPath = "deploy/production-invariants.json",
    [ValidateSet("preflight", "record")]
    [string]$Mode = "preflight",
    [ValidateSet("backend", "frontend", "full")]
    [string]$Surface = "full",
    [string]$LedgerPath,
    [string]$RailwayDeploymentId,
    [string]$RailwayStatus,
    [string]$VercelDeploymentId,
    [string]$VercelStatus,
    [ValidateSet("PASS", "NOT_APPLICABLE", "NOT_CONFIRMED")]
    [string]$BackendRuntimeProof = "NOT_CONFIRMED",
    [ValidateSet("PASS", "NOT_APPLICABLE", "NOT_CONFIRMED")]
    [string]$FrontendRuntimeProof = "NOT_CONFIRMED",
    [ValidateSet("PASS", "NOT_APPLICABLE", "NOT_CONFIRMED")]
    [string]$AuthenticatedUiProof = "NOT_CONFIRMED"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = & git -C $RepositoryRoot @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    return ($output -join "`n").Trim()
}

function Assert-Equal {
    param(
        [Parameter(Mandatory = $true)][string]$Actual,
        [Parameter(Mandatory = $true)][string]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ($Actual -ne $Expected) {
        throw "$Label must be '$Expected', got '$Actual'."
    }
}

$RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$manifestFile = Join-Path $RepositoryRoot $ManifestPath
if (-not (Test-Path -LiteralPath $manifestFile -PathType Leaf)) {
    throw "Deployment invariant manifest not found: $manifestFile"
}

$manifest = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
$headCommit = Invoke-Git @("rev-parse", "HEAD")
$expectedCommit = Invoke-Git @("rev-parse", $ExpectedRemoteRef)
$worktreeStatus = Invoke-Git @("status", "--porcelain")

if ($worktreeStatus) {
    throw "Deployment worktree is dirty. Commit or remove local changes before deploying."
}
Assert-Equal -Actual $headCommit -Expected $expectedCommit -Label "Deployment commit"

foreach ($feature in $manifest.required_features) {
    foreach ($fileRule in $feature.files) {
        $candidate = Join-Path $RepositoryRoot ([string]$fileRule.path)
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "Required feature '$($feature.id)' is missing file '$($fileRule.path)'."
        }

        $content = Get-Content -LiteralPath $candidate -Raw
        foreach ($marker in $fileRule.contains_all) {
            if (-not $content.Contains([string]$marker)) {
                throw "Required feature '$($feature.id)' is missing marker '$marker' in '$($fileRule.path)'."
            }
        }
    }
}

if ($LedgerPath -and (Test-Path -LiteralPath $LedgerPath -PathType Leaf)) {
    $lastLine = Get-Content -LiteralPath $LedgerPath | Where-Object { $_.Trim() } | Select-Object -Last 1
    if ($lastLine) {
        $previous = $lastLine | ConvertFrom-Json
        if ($previous.commit) {
            & git -C $RepositoryRoot merge-base --is-ancestor ([string]$previous.commit) $headCommit
            if ($LASTEXITCODE -ne 0) {
                throw "Candidate $headCommit is not a descendant of the last recorded production commit $($previous.commit)."
            }
        }
    }
}

if ($Mode -eq "record") {
    if (-not $LedgerPath) {
        throw "LedgerPath is required in record mode."
    }

    if ($Surface -in @("backend", "full")) {
        Assert-Equal -Actual $RailwayStatus -Expected "SUCCESS" -Label "Railway status"
        Assert-Equal -Actual $BackendRuntimeProof -Expected "PASS" -Label "Backend runtime proof"
        if (-not $RailwayDeploymentId) {
            throw "RailwayDeploymentId is required for backend releases."
        }
    }

    if ($Surface -in @("frontend", "full")) {
        Assert-Equal -Actual $VercelStatus -Expected "READY" -Label "Vercel status"
        Assert-Equal -Actual $FrontendRuntimeProof -Expected "PASS" -Label "Frontend runtime proof"
        Assert-Equal -Actual $AuthenticatedUiProof -Expected "PASS" -Label "Authenticated UI proof"
        if (-not $VercelDeploymentId) {
            throw "VercelDeploymentId is required for frontend releases."
        }
    }

    $ledgerDirectory = Split-Path -Parent $LedgerPath
    if ($ledgerDirectory -and -not (Test-Path -LiteralPath $ledgerDirectory)) {
        New-Item -ItemType Directory -Path $ledgerDirectory -Force | Out-Null
    }

    $record = [ordered]@{
        recorded_at_utc = (Get-Date).ToUniversalTime().ToString("o")
        repository = $RepositoryRoot
        canonical_ref = $ExpectedRemoteRef
        commit = $headCommit
        surface = $Surface
        manifest_schema_version = $manifest.schema_version
        required_features = @($manifest.required_features | ForEach-Object { $_.id })
        railway = [ordered]@{
            deployment_id = $RailwayDeploymentId
            status = $RailwayStatus
            runtime_proof = $BackendRuntimeProof
        }
        vercel = [ordered]@{
            deployment_id = $VercelDeploymentId
            status = $VercelStatus
            runtime_proof = $FrontendRuntimeProof
            authenticated_ui_proof = $AuthenticatedUiProof
        }
    }
    Add-Content -LiteralPath $LedgerPath -Value ($record | ConvertTo-Json -Depth 6 -Compress)
}

[ordered]@{
    status = "PASS"
    mode = $Mode
    surface = $Surface
    commit = $headCommit
    canonical_ref = $ExpectedRemoteRef
    required_features = @($manifest.required_features | ForEach-Object { $_.id })
} | ConvertTo-Json -Depth 4
