# Register GitHub Secrets from .env (requires gh CLI, logged in to the repo owner)
# Run from VS Code terminal:
#   powershell -ExecutionPolicy Bypass -File reel-bot\scripts\set-github-secrets.ps1

$ErrorActionPreference = "Stop"
$repo = "JourneysPartner/reel-automation"

# Resolve paths (this script lives in ig-tax-guardian/reel-bot/scripts)
$igRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
$envPath = Join-Path $igRoot ".env"
$parent = (Resolve-Path "$igRoot\..").Path

if (-not (Test-Path $envPath)) { throw ".env not found: $envPath" }

# Load .env (KEY=VALUE)
$envMap = @{}
Get-Content $envPath | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    $envMap[$matches[1]] = $matches[2].Trim()
  }
}

# Keys to register (must match .env names)
$keys = @(
  "ANTHROPIC_API_KEY",
  "GCP_PROJECT_ID", "GCS_BUCKET_NAME",
  "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN",
  "GSHEET_ID", "CHATWORK_API_TOKEN", "CHATWORK_ROOM_ID",
  "META_ACCESS_TOKEN", "INSTAGRAM_BUSINESS_ACCOUNT_ID",
  "APPROVAL_BASE_URL", "APPROVAL_SECRET"
)

foreach ($k in $keys) {
  if ($envMap.ContainsKey($k) -and $envMap[$k]) {
    gh secret set $k --repo $repo --body $envMap[$k]
    Write-Host "  set $k" -ForegroundColor Green
  } else {
    Write-Host "  SKIP $k (no value in .env)" -ForegroundColor Yellow
  }
}

# GCP_SA_KEY = full service-account JSON file content (multi-line)
$saPath = $envMap["GOOGLE_APPLICATION_CREDENTIALS"]
if ($saPath -and -not (Test-Path $saPath) -and (Test-Path "$saPath.json")) { $saPath = "$saPath.json" }
if (-not $saPath -or -not (Test-Path $saPath)) {
  $found = Get-ChildItem $parent -Filter "reels-automation-*.json" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($found) { $saPath = $found.FullName }
}
if ($saPath -and (Test-Path $saPath)) {
  Get-Content $saPath -Raw | gh secret set GCP_SA_KEY --repo $repo
  Write-Host "  set GCP_SA_KEY (from $saPath)" -ForegroundColor Green
} else {
  Write-Host "  SKIP GCP_SA_KEY (key file not found)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. Verify with: gh secret list --repo $repo" -ForegroundColor Cyan
