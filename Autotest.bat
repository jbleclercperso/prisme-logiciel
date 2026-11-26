@echo off
chcp 65001 >nul
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m jarvis --autotest %*
echo.
echo Rapport : data\autotest.txt
pause
