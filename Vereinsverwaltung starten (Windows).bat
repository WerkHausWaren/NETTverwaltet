@echo off
REM Doppelklick-Starter fuer die Vereinsverwaltung (Windows).
REM Beim ersten Start wird automatisch alles eingerichtet.
chcp 65001 >nul
cd /d "%~dp0"

echo ======================================================
echo    Vereinsverwaltung wird gestartet ...
echo ======================================================

REM 1) Python vorhanden?
where py >nul 2>&1
if %errorlevel%==0 (
    set "PYCMD=py -3"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        set "PYCMD=python"
    ) else (
        echo.
        echo   [!] Python 3 wurde nicht gefunden.
        echo   Bitte einmalig von https://www.python.org/downloads/ installieren.
        echo   WICHTIG: beim Installieren "Add Python to PATH" anhaken.
        echo   Danach dieses Fenster erneut oeffnen.
        echo.
        pause
        exit /b 1
    )
)

REM 2) Virtuelle Umgebung anlegen, falls noch nicht vorhanden
if not exist ".venv\" (
    echo Erste Einrichtung: virtuelle Umgebung wird erstellt ...
    %PYCMD% -m venv .venv
)

REM 3) Pakete installieren, falls noch nicht geschehen
if not exist ".venv\.installiert" (
    echo Benoetigte Pakete werden installiert ^(einmalig, Internet noetig^) ...
    call ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    call ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
    if %errorlevel%==0 (
        echo. > ".venv\.installiert"
        echo Fertig eingerichtet.
    ) else (
        echo [!] Einige Pakete konnten nicht installiert werden - die App startet trotzdem.
    )
)

REM 4) App starten
call ".venv\Scripts\python.exe" run.py
pause
