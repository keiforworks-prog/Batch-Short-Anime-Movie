@echo off
chcp 65001 > nul
echo ========================================
echo  台本アップロード to GCS
echo ========================================
echo.

set BUCKET_NAME=ai-image-pipeline-scripts

REM ドラッグ&ドロップされたファイルを取得
if "%~1"=="" (
    echo ❌ 使い方: 台本ファイルをこのバッチファイルにドラッグ&ドロップしてください
    echo.
    echo.
echo ========================================
echo 処理完了 - このウィンドウは手動で閉じてください
echo ========================================
    exit /b 1
)

set FILENAME=%~1

REM ファイルの存在確認
if not exist "%FILENAME%" (
    echo ❌ ファイルが見つかりません: %FILENAME%
    echo.
    pause
    exit /b 1
)

REM .txt ファイルかチェック
if /I not "%~x1"==".txt" (
    echo ⚠️ 警告: .txt ファイルではありません
    echo ファイル: %~nx1
    echo.
    choice /C YN /M "このままアップロードしますか？"
    if errorlevel 2 exit /b 1
)

echo 📄 ファイル: %~nx1
echo 📦 アップロード先: gs://%BUCKET_NAME%/input/
echo.
echo アップロード中...
echo.

REM 強制上書きでアップロード
gsutil cp "%FILENAME%" gs://%BUCKET_NAME%/input/ 2>&1

set UPLOAD_RESULT=%errorlevel%

echo.
echo ----------------------------------------
if %UPLOAD_RESULT% equ 0 (
    echo ✅ アップロード成功！
    echo.
    echo 📦 アップロード先:
    echo    gs://%BUCKET_NAME%/input/%~nx1
    echo.
    echo 🚀 次に実行:
    echo    gcloud run jobs execute ai-image-pipeline
    echo    --region asia-northeast1 --wait
) else (
    echo ❌ アップロード失敗 (エラーコード: %UPLOAD_RESULT%)
)
echo ----------------------------------------
echo.

pause