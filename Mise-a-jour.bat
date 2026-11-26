@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Mise a jour de Jarvis ===
if not exist .git (echo Ce dossier n a pas ete installe avec git : telechargez la nouvelle archive. & pause & exit /b 1)
git pull || (echo Echec de git pull & pause & exit /b 1)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
pip install -q -r requirements-voix.txt
python -m jarvis --diagnostic
pause
