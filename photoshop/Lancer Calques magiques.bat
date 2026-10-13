@echo off
rem Calques magiques - lance la fenetre (Photoshop doit etre ouvert avec une image)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 CalquesMagiques.py
) else (
  python CalquesMagiques.py
)
if errorlevel 1 pause
