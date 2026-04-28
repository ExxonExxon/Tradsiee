#!/bin/bash

# 1. Pull the latest code from the main branch
git pull origin main

# 2. Install/Update dependencies
source venv/bin/activate
pip install -r requirements.txt

# 3. Reload or Start the process
# We try to reload first (Zero Downtime). 
# If it fails (first time), we start it.
pm2 reload tradsiee-api --update-env || pm2 start "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1" --name tradsiee-api

# 4. Save the PM2 list so it persists after reboots
pm2 save
