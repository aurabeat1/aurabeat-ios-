@echo off
echo Stopping AuraBeat Addon Server...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :3000 ^| findstr LISTENING') do taskkill /F /PID %%a 2>nul
taskkill /F /IM node.exe 2>nul
echo Server stopped.
pause
