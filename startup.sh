#!/bin/bash
pip install -r /home/site/wwwroot/requirements.txt
pip install /home/site/wwwroot/
python -m streamlit run clients/app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false
