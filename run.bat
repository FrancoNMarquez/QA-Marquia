@echo off
REM Webchat QA - arranque de un solo paso (Windows).
REM Doble-click y listo: crea el entorno si falta, instala lo necesario y abre la app.
setlocal

cd /d "%~dp0"

set "VENV=venv"
set "PY=%VENV%\Scripts\python.exe"
set "PIP=%VENV%\Scripts\pip.exe"
set "STREAMLIT=%VENV%\Scripts\streamlit.exe"
set "PLAYWRIGHT=%VENV%\Scripts\playwright.exe"

echo Webchat QA

REM 1) Crear el venv si no existe.
if not exist "%PY%" (
  echo - Creando entorno virtual...
  python -m venv "%VENV%"
)

REM 2) Instalar dependencias si todavia no estan (marca con un archivo sentinela).
if not exist "%VENV%\.deps_installed" (
  echo - Instalando dependencias...
  "%PIP%" install -q -r requirements.txt
  "%PLAYWRIGHT%" install chromium
  echo ok> "%VENV%\.deps_installed"
)

REM 3) Abrir el navegador (con un pequeno delay) y levantar la app.
echo - Abriendo http://localhost:8501  -  Ctrl+C para cortar
start "" /b cmd /c "timeout /t 3 >nul & start http://localhost:8501"
"%STREAMLIT%" run app.py

endlocal
