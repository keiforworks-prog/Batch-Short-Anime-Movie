@echo off
chcp 65001 >nul
echo ========================================
echo ANIME SHORT - 実行開始
echo ========================================
echo.
gcloud run jobs execute anime-short --region asia-northeast1 --project ai-content-pipeline-475505 --wait
echo.
echo ========================================
echo 完了
echo ========================================
pause
