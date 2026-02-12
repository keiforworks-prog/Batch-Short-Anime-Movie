@echo off
chcp 65001 > nul
echo ========================================
echo  AI画像生成パイプライン 実行
echo ========================================
echo.

cd scripts

python main_pipeline.py normal

echo.
pause