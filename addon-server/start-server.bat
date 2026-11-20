@echo off
cd /d "%~dp0"
set BITCHORD_AUTH_MODE=log
echo ==============================================
echo  AuraBeat / BitChord Addon Server (Log Mode)
echo ==============================================
echo.
npm start
pause
