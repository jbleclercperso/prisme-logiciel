@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Installation de Jarvis ===
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version || (echo Python introuvable. Installez Python 3.11+ depuis https://www.python.org/downloads/ en cochant "Add to PATH". & pause & exit /b 1)
%PY% -m venv .venv || (echo Echec de creation du venv & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo Echec installation du coeur & pause & exit /b 1)
pip install -r requirements-voix.txt || echo [!] Une partie de la voix n'a pas pu s'installer : le mode texte fonctionnera quand meme.
if not exist .env copy .env.example .env >nul
echo.
echo === Termine. ===
powershell -NoProfile -ExecutionPolicy Bypass -File outils\raccourci.ps1
python -m jarvis --connexion
python -m jarvis --diagnostic
echo.
echo Lancez Jarvis.bat (voix) ou Jarvis-texte.bat (clavier).
pause
