@echo off
cd /d "%~dp0"
echo Freeing port 3000...
taskkill /F /IM node.exe 2>nul
set BITCHORD_AUTH_MODE=log
echo ==============================================
echo  AuraBeat / BitChord Addon Server (Log Mode)
echo ==============================================
echo.
npm start
pause
