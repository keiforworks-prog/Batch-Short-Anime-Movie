@echo off
setlocal

echo ============================================================
echo Git Commit Helper
echo ============================================================
echo.

REM Move to script directory
cd /d "%~dp0"

REM Show current status
echo Current changes:
echo.
"C:\Program Files\Git\cmd\git.exe" status
echo.
echo ============================================================
echo.

REM Get commit message
set /p COMMIT_MSG="Enter commit message: "

if "%COMMIT_MSG%"=="" (
    echo.
    echo ERROR: Commit message is empty
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo Confirmation
echo ============================================================
echo Commit message: %COMMIT_MSG%
echo.
choice /C YN /M "Commit with this message"
if errorlevel 2 (
    echo.
    echo Cancelled
    echo.
    pause
    exit /b 0
)

echo.
echo ============================================================
echo Step 1/3: Staging changes...
echo ============================================================
echo.

"C:\Program Files\Git\cmd\git.exe" add .

if errorlevel 1 (
    echo.
    echo ERROR: git add failed
    echo.
    pause
    exit /b 1
)

echo Staging completed
echo.

echo ============================================================
echo Step 2/3: Committing...
echo ============================================================
echo.

"C:\Program Files\Git\cmd\git.exe" commit -m "%COMMIT_MSG%"

if errorlevel 1 (
    echo.
    echo ERROR: git commit failed
    echo.
    pause
    exit /b 1
)

echo Commit completed
echo.

echo ============================================================
echo Step 3/3: Pushing to remote...
echo ============================================================
echo.

"C:\Program Files\Git\cmd\git.exe" push

if errorlevel 1 (
    echo.
    echo ERROR: git push failed
    echo.
    pause
    exit /b 1
)

echo Push completed
echo.

echo ============================================================
echo All processes completed successfully!
echo ============================================================
echo.
echo Final status:
"C:\Program Files\Git\cmd\git.exe" status
echo.

pause