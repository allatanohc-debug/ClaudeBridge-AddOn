@echo off
setlocal EnableDelayedExpansion
rem Calques magiques - lance la fenetre (Photoshop doit etre ouvert avec une image)
cd /d "%~dp0"
set "PYEXE="
set "PYARG="

call :chercher
if not defined PYEXE (
  echo.
  echo Python n'est pas installe sur cet ordinateur ^(il en faut un vrai, pas le raccourci du Microsoft Store^).
  echo Installation automatique de Python 3.12 ^(une seule fois, environ 2 minutes^)...
  echo.
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  call :chercher
)
if not defined PYEXE (
  echo.
  echo Impossible de trouver Python. Installe-le depuis https://www.python.org/downloads/
  echo ^(coche "Add python.exe to PATH" pendant l'installation^), puis relance ce fichier.
  pause
  exit /b 1
)
echo Python : %PYEXE% %PYARG%
"%PYEXE%" %PYARG% CalquesMagiques.py
if errorlevel 1 pause
exit /b 0

:chercher
rem 1) installations classiques de python.org (la plus recente gagne)
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*" "%ProgramFiles%\Python3*" "C:\Python3*") do (
  if exist "%%~D\python.exe" set "PYEXE=%%~D\python.exe"
)
if defined PYEXE exit /b 0
rem 2) lanceur py
where py >nul 2>nul
if !errorlevel!==0 (
  set "PYEXE=py"
  set "PYARG=-3"
  exit /b 0
)
rem 3) python dans le PATH, sauf le faux raccourci du Microsoft Store (WindowsApps)
for /f "delims=" %%P in ('where python 2^>nul') do (
  echo %%P | find /i "WindowsApps" >nul
  if errorlevel 1 if not defined PYEXE set "PYEXE=%%P"
)
exit /b 0
