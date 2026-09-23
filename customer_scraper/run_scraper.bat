@echo off
setlocal

:: ===================== EDIT IF NEEDED =====================
set PROJECT_DIR=C:\Users\Sujal.bangde\Desktop\FK_NEXT_GEN\FK_NEXT_GEN_SCRAPPING\customer_scraper
set CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe
set DEBUG_PORT=9222
:: ============================================================

echo.
echo [1/4] Closing any existing Chrome windows...
taskkill /F /IM chrome.exe >nul 2>&1

echo.
echo [2/4] Launching Chrome with remote debugging on port %DEBUG_PORT%...
start "" "%CHROME_EXE%" --remote-debugging-port=%DEBUG_PORT% --user-data-dir="%TEMP%\ChromeDebug"

echo.
echo A new Chrome window just opened using a temporary profile.
echo Please log in to the Flipkart seller-support portal in that window now.
echo.
pause

echo.
echo [3/4] Verifying the debug port is alive...
powershell -NoProfile -Command "try { $r = Invoke-WebRequest ('http://127.0.0.1:' + $env:DEBUG_PORT + '/json/version') -UseBasicParsing; Write-Host $r.Content } catch { Write-Host 'ERROR: Could not reach the debug port. Is Chrome still open?' -ForegroundColor Red; exit 1 }"

if errorlevel 1 (
    echo.
    echo Debug port check failed. Aborting before running the scraper.
    pause
    exit /b 1
)

echo.
echo [4/4] Starting the scraper...
cd /d "%PROJECT_DIR%"
python main.py

echo.
echo Scraper finished or exited.
pause >nul
