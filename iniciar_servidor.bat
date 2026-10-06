@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo No se encontro venv\Scripts\python.exe -- el entorno virtual no esta creado.
    echo Corre primero: python -m venv venv
    echo Y luego:       venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

rem Se invoca venv\Scripts\python.exe con su ruta completa (no solo "python")
rem a proposito: en esta PC, Windows a veces resuelve "python" al alias de
rem Microsoft Store en vez de al venv activado, aunque "activate.bat" ya
rem corrio -- ese Python global no tiene instalados qrcode ni cryptography,
rem y la app falla a medias (arranca, pero truena en las rutas que los usan).
rem Llamar al python.exe del venv por su ruta completa evita ese problema
rem por completo, sin depender de como Windows resuelva PATH.
venv\Scripts\python.exe run.py
pause
