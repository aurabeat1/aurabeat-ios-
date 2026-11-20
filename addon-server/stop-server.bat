@echo off
echo Stopping AuraBeat Addon Server...
taskkill /F /IM node.exe 2>nul
echo Server stopped.
pause
