@echo off
rem Lance Jarvis automatiquement a l ouverture de session Windows
set CIBLE=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Jarvis.bat
echo @echo off> "%CIBLE%"
echo start "Jarvis" /min "%~dp0Jarvis.bat">> "%CIBLE%"
echo Jarvis demarrera avec Windows. Pour annuler, supprimez : %CIBLE%
pause
