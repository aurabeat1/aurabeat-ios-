@echo off
cd /d "%~dp0"
echo Freeing port 3000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :3000 ^| findstr LISTENING') do taskkill /F /PID %%a 2>nul
set BITCHORD_AUTH_MODE=log
echo ==============================================
echo  AuraBeat Python Addon Server (FastAPI)
echo ==============================================
echo.
python -m uvicorn main:app --port 3000 --host 0.0.0.0 --reload
pause
