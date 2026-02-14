@echo off
chcp 65001 >nul
echo ========================================
echo AI Image Pipeline - ANIME SHORT VERSION
echo ========================================
echo.
echo [1/3] Docker image build...
docker build -t gcr.io/ai-content-pipeline-475505/ai-image-pipeline:anime-short .
if %ERRORLEVEL% NEQ 0 (
    echo BUILD FAILED
    pause
    exit /b 1
)
echo.
echo [2/3] GCR push...
docker push gcr.io/ai-content-pipeline-475505/ai-image-pipeline:anime-short
if %ERRORLEVEL% NEQ 0 (
    echo PUSH FAILED
    pause
    exit /b 1
)
echo.
echo [3/3] Cloud Run Job deploy...
gcloud run jobs deploy anime-short --image gcr.io/ai-content-pipeline-475505/ai-image-pipeline:anime-short --region asia-northeast1 --project ai-content-pipeline-475505 --max-retries 0 --task-timeout 24h --memory 2Gi --cpu 1 --set-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest,MINIMAX_API_KEY=MINIMAX_API_KEY:latest,GDRIVE_PARENT_FOLDER_ID=GDRIVE_PARENT_FOLDER_ID:latest,DISCORD_WEBHOOK_URL_DIRECT_ANIME=DISCORD_WEBHOOK_URL_DIRECT_ANIME:latest --set-secrets=/secrets/creds/credentials.json=google-credentials:latest,/secrets/token/gdrive_token.json=google-token:latest
if %ERRORLEVEL% NEQ 0 (
    echo DEPLOY FAILED
    pause
    exit /b 1
)
echo.
echo DEPLOY COMPLETE
echo.
echo Run: gcloud run jobs execute anime-short --region asia-northeast1 --wait
echo.
pause
