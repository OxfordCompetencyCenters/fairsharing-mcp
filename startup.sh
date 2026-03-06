#!/bin/bash
pip install -r /home/site/wwwroot/requirements.txt
pip install /home/site/wwwroot/

if [ "$MCP_TRANSPORT" = "streamable-http" ]; then
    # Run as remote MCP server (HTTP endpoint at /mcp on port 8000)
    python -m fairsharing_mcp.server
else
    # Run Streamlit frontend (default)
    python -m streamlit run clients/app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false
fi
