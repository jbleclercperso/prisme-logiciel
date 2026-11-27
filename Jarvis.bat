@echo off
rem Lance Jarvis sans fenetre de terminal. Pour voir les messages techniques : Jarvis-debug.bat
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m jarvis %*
