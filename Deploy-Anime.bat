@echo off
chcp 65001 >nul
echo ========================================
echo AI Image Pipeline - ANIME VERSION
echo ========================================

echo.
echo [1/3] Docker イメージをビルド中...
docker build -t gcr.io/ai-content-pipeline-475505/ai-image-pipeline:anime .

if %ERRORLEVEL% NEQ 0 (
    echo ❌ ビルドに失敗しました
    pause
    exit /b 1
)

echo.
echo [2/3] GCR にプッシュ中...
docker push gcr.io/ai-content-pipeline-475505/ai-image-pipeline:anime

if %ERRORLEVEL% NEQ 0 (
    echo ❌ プッシュに失敗しました
    pause
    exit /b 1
)

echo.
echo [3/3] Cloud Run Job を作成/更新中...
gcloud run jobs deploy ai-image-pipeline-anime ^
  --image gcr.io/ai-content-pipeline-475505/ai-image-pipeline:anime ^
  --region asia-northeast1 ^
  --project ai-content-pipeline-475505 ^
  --max-retries 0 ^
  --task-timeout 24h ^
  --memory 2Gi ^
  --cpu 1 ^
  --set-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest,GDRIVE_PARENT_FOLDER_ID=GDRIVE_PARENT_FOLDER_ID:latest,DISCORD_WEBHOOK_URL_DIRECT=DISCORD_WEBHOOK_URL_DIRECT:latest ^
  --set-secrets=/secrets/creds/credentials.json=google-credentials:latest,/secrets/token/gdrive_token.json=google-token:latest ^

if %ERRORLEVEL% NEQ 0 (
    echo ❌ デプロイに失敗しました
    pause
    exit /b 1
)

echo.
echo ✅ デプロイ完了！
echo.
echo 実行方法:
echo gcloud run jobs execute ai-image-pipeline-anime --region asia-northeast1 --wait
echo.
pause