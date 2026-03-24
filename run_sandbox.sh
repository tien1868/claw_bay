#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# eBay Claw - Sandbox Testing Launcher (bash version)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Quick start script for running Claw Bay in Sandbox mode
#
# Usage: ./run_sandbox.sh
#
# ═══════════════════════════════════════════════════════════════════════════════

set -e

echo "═══════════════════════════════════════════════════════════════"
echo "  eBay Claw - Sandbox Testing Mode"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "[ERROR] Virtual environment not found"
    echo ""
    echo "Run setup first:"
    echo "  python -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -e \".[dev]\""
    echo ""
    exit 1
fi

# Check if api.env exists
if [ ! -f "api.env" ]; then
    echo "[WARNING] api.env not found"
    echo ""
    echo "Create api.env from template:"
    echo "  cp api.env.sandbox.template api.env"
    echo ""
    echo "Then edit api.env with your Sandbox credentials"
    echo ""

    read -p "Create api.env from template now? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cp api.env.sandbox.template api.env
        echo "[OK] Created api.env - edit it with your Sandbox credentials"
        echo ""
        exit 0
    else
        exit 1
    fi
fi

# Check if api.env has been configured
if grep -q "YOUR_SANDBOX_CLIENT_ID_HERE" api.env; then
    echo "[ERROR] api.env still contains template placeholders"
    echo ""
    echo "Edit api.env and replace:"
    echo "  - YOUR_SANDBOX_CLIENT_ID_HERE"
    echo "  - YOUR_SANDBOX_CLIENT_SECRET_HERE"
    echo "  - YOUR_SANDBOX_REFRESH_TOKEN_HERE"
    echo ""
    echo "Get credentials from:"
    echo "  https://developer.ebay.com/my/keys (Sandbox tab)"
    echo ""
    exit 1
fi

# Activate virtual environment
echo "[OK] Activating virtual environment..."
source .venv/bin/activate

# Verify Streamlit is installed
if ! python -c "import streamlit" 2>/dev/null; then
    echo "[ERROR] Streamlit not installed"
    echo ""
    echo "Install dependencies:"
    echo "  pip install -e \".[dev]\""
    echo ""
    exit 1
fi

STREAMLIT_VERSION=$(python -c "import streamlit; print(streamlit.__version__)")
echo "[OK] Streamlit version: $STREAMLIT_VERSION"
echo ""

# Run secret scanner
echo "[INFO] Running secret scanner..."
python check_secrets.py || true
echo ""

# Display configuration
echo "═══════════════════════════════════════════════════════════════"
echo "  Configuration"
echo "═══════════════════════════════════════════════════════════════"

RUNTIME_MODE=$(grep "EBAY_CLAW_RUNTIME_MODE=" api.env | cut -d'=' -f2)
USE_SANDBOX=$(grep "EBAY_CLAW_EBAY_USE_SANDBOX=" api.env | cut -d'=' -f2)
EXECUTION_ENABLED=$(grep "EBAY_CLAW_EXECUTION_ENABLED=" api.env | cut -d'=' -f2)

echo "Runtime Mode: $RUNTIME_MODE"
echo "Sandbox Mode: $USE_SANDBOX"
echo "Execution:    $EXECUTION_ENABLED"
echo ""

# Confirm before starting
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Starting Streamlit dashboard..."
echo ""
echo "Dashboard will open at: http://localhost:8501"
echo "Press Ctrl+C to stop"
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo ""

sleep 2

# Launch Streamlit
streamlit run ebay_claw/app/streamlit_app.py --server.headless true
