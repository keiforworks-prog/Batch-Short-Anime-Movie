#!/bin/bash
set -e
echo "--- Starting container ---"
pwd
ls -lah
echo "--- Copying credentials ---"
cp /secrets/creds/credentials.json scripts/credentials.json
cp /secrets/token/gdrive_token.json scripts/gdrive_token.json
echo "--- Credentials copied ---"
echo "--- Checking credentials ---"
ls -lah scripts/credentials.json
ls -lah scripts/gdrive_token.json
echo "--- Credentials OK ---"
echo "--- Environment Variables ---"
echo "K_SERVICE: $K_SERVICE"
echo "---"
echo "--- Starting Python script ---"
cd scripts
python main_pipeline.py batch
echo "--- Script finished ---"