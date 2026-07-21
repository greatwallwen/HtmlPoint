@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-course-studio.ps1"
exit /b %ERRORLEVEL%
