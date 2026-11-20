@echo off
cd /d "%~dp0"
python setup_autostart.py uninstall
echo Stopping running server process on port 3000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :3000 ^| findstr LISTENING') do taskkill /F /PID %%a 2>nul
taskkill /F /IM node.exe 2>nul
echo Done.
pause
