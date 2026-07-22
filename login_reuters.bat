@echo off
rem === Login manual de Reuters (Windows) ===
rem Abre una ventana de Chrome con el perfil de la app en la pagina de login de
rem Reuters Connect. Inicia sesion a mano (email, contrasena y el deslizador si
rem aparece). La sesion queda guardada y las siguientes ejecuciones la reutilizan.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo No se encuentra el entorno virtual .venv. Ejecuta primero arrancar_servidor.bat.
  pause
  exit /b 1
)

.venv\Scripts\python.exe -m scripts.login_reuters
pause
