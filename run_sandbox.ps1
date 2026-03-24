# ═══════════════════════════════════════════════════════════════════════════════
# eBay Claw - Sandbox Testing Launcher
# ═══════════════════════════════════════════════════════════════════════════════
#
# Quick start script for running Claw Bay in Sandbox mode
#
# Usage: .\run_sandbox.ps1
#
# ═══════════════════════════════════════════════════════════════════════════════

Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  eBay Claw - Sandbox Testing Mode" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# Check if virtual environment exists
if (-Not (Test-Path ".venv")) {
    Write-Host "[ERROR] Virtual environment not found" -ForegroundColor Red
    Write-Host ""
    Write-Host "Run setup first:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv" -ForegroundColor White
    Write-Host "  .venv\Scripts\Activate.ps1" -ForegroundColor White
    Write-Host "  pip install -e "".[dev]""" -ForegroundColor White
    Write-Host ""
    exit 1
}

# Check if api.env exists
if (-Not (Test-Path "api.env")) {
    Write-Host "[WARNING] api.env not found" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Create api.env from template:" -ForegroundColor Yellow
    Write-Host "  cp api.env.sandbox.template api.env" -ForegroundColor White
    Write-Host ""
    Write-Host "Then edit api.env with your Sandbox credentials" -ForegroundColor Yellow
    Write-Host ""

    # Offer to create it
    $response = Read-Host "Create api.env from template now? (y/n)"
    if ($response -eq "y") {
        Copy-Item "api.env.sandbox.template" "api.env"
        Write-Host "[OK] Created api.env - edit it with your Sandbox credentials" -ForegroundColor Green
        Write-Host ""
        exit 0
    } else {
        exit 1
    }
}

# Check if api.env has been configured
$envContent = Get-Content "api.env" -Raw
if ($envContent -match "YOUR_SANDBOX_CLIENT_ID_HERE") {
    Write-Host "[ERROR] api.env still contains template placeholders" -ForegroundColor Red
    Write-Host ""
    Write-Host "Edit api.env and replace:" -ForegroundColor Yellow
    Write-Host "  - YOUR_SANDBOX_CLIENT_ID_HERE" -ForegroundColor White
    Write-Host "  - YOUR_SANDBOX_CLIENT_SECRET_HERE" -ForegroundColor White
    Write-Host "  - YOUR_SANDBOX_REFRESH_TOKEN_HERE" -ForegroundColor White
    Write-Host ""
    Write-Host "Get credentials from:" -ForegroundColor Yellow
    Write-Host "  https://developer.ebay.com/my/keys (Sandbox tab)" -ForegroundColor White
    Write-Host ""
    exit 1
}

# Activate virtual environment
Write-Host "[OK] Activating virtual environment..." -ForegroundColor Green
& .venv\Scripts\Activate.ps1

# Verify Streamlit is installed
$streamlitCheck = python -c "import streamlit; print(streamlit.__version__)" 2>$null
if (-Not $streamlitCheck) {
    Write-Host "[ERROR] Streamlit not installed" -ForegroundColor Red
    Write-Host ""
    Write-Host "Install dependencies:" -ForegroundColor Yellow
    Write-Host "  pip install -e "".[dev]""" -ForegroundColor White
    Write-Host ""
    exit 1
}

Write-Host "[OK] Streamlit version: $streamlitCheck" -ForegroundColor Green
Write-Host ""

# Run secret scanner
Write-Host "[INFO] Running secret scanner..." -ForegroundColor Cyan
python check_secrets.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[WARNING] Secret scanner found issues" -ForegroundColor Yellow
    Write-Host "This is expected if api.env exists (it's gitignored)" -ForegroundColor Yellow
    Write-Host ""
}

# Display configuration
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Configuration" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan

$runtimeMode = Select-String -Path "api.env" -Pattern "EBAY_CLAW_RUNTIME_MODE=(.+)" | ForEach-Object { $_.Matches.Groups[1].Value }
$useSandbox = Select-String -Path "api.env" -Pattern "EBAY_CLAW_EBAY_USE_SANDBOX=(.+)" | ForEach-Object { $_.Matches.Groups[1].Value }
$executionEnabled = Select-String -Path "api.env" -Pattern "EBAY_CLAW_EXECUTION_ENABLED=(.+)" | ForEach-Object { $_.Matches.Groups[1].Value }

Write-Host "Runtime Mode: " -NoNewline -ForegroundColor White
Write-Host $runtimeMode -ForegroundColor $(if ($runtimeMode -eq "live_read_only") { "Green" } else { "Yellow" })

Write-Host "Sandbox Mode: " -NoNewline -ForegroundColor White
Write-Host $useSandbox -ForegroundColor $(if ($useSandbox -eq "true") { "Green" } else { "Red" })

Write-Host "Execution:    " -NoNewline -ForegroundColor White
Write-Host $executionEnabled -ForegroundColor $(if ($executionEnabled -eq "false") { "Green" } else { "Yellow" })

Write-Host ""

# Confirm before starting
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "Starting Streamlit dashboard..." -ForegroundColor Green
Write-Host ""
Write-Host "Dashboard will open at: http://localhost:8501" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

Start-Sleep -Seconds 2

# Launch Streamlit
streamlit run ebay_claw/app/streamlit_app.py --server.headless true
