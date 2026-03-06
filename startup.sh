#!/bin/bash
# Find the app directory (Oryx may extract to /tmp/ or keep in /home/site/wwwroot/)
APP_DIR="/home/site/wwwroot"
[ -f "$APP_DIR/requirements.txt" ] || APP_DIR="$(pwd)"

pip install -r "$APP_DIR/requirements.txt" 2>/dev/null || true
pip install --no-cache-dir "$APP_DIR/"

echo "MCP_TRANSPORT=$MCP_TRANSPORT"

if [ "$MCP_TRANSPORT" = "streamable-http" ]; then
    # Run as remote MCP server (HTTP endpoint at /mcp on port 8000)
    python -m fairsharing_mcp.server
else
    # Run Streamlit frontend (default)
    python -m streamlit run "$APP_DIR/clients/app.py" --server.port 8000 --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false
fi
