@echo off
rem === newsphotostalker (Windows / GUI) ===
rem Arranca el servidor web y abre el navegador.
rem Deja ESTA ventana abierta: al cerrarla se detiene el servidor.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo No se encuentra el entorno virtual .venv en esta carpeta.
  echo Preparalo una sola vez con:
  echo     python -m venv .venv
  echo     .venv\Scripts\python -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

if not exist "config.local.yaml" (
  echo.
  echo Falta config.local.yaml. Copia config.example.yaml a config.local.yaml
  echo y rellena tus credenciales de Reuters.
  echo.
  pause
  exit /b 1
)

echo Arrancando newsphotostalker en http://127.0.0.1:8010 ...
start "" http://127.0.0.1:8010
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
pause
