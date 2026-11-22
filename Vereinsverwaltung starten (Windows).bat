@echo off
REM Doppelklick-Starter fuer die Vereinsverwaltung (Windows).
REM Beim ersten Start wird automatisch alles eingerichtet.
REM Hinweis fuer Bastler:innen: Fehlercodes werden mit "if errorlevel" geprueft, weil
REM %errorlevel% in Klammerbloecken schon beim Einlesen ausgewertet wird (klassische Falle).
chcp 65001 >nul
cd /d "%~dp0"

echo ======================================================
echo    Vereinsverwaltung wird gestartet ...
echo ======================================================

REM 1) Python vorhanden? Erst den Python-Starter "py", dann "python" pruefen.
REM    "--version" stellt sicher, dass es ein echtes Python ist (nicht nur der Store-Verweis).
set "PYCMD="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto python_ok
python --version >nul 2>&1
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto python_ok
echo.
echo   [!] Python 3 wurde nicht gefunden.
echo   Bitte einmalig von https://www.python.org/downloads/ installieren.
echo   WICHTIG: beim Installieren "Add Python to PATH" anhaken.
echo   Danach den Rechner neu starten und diese Datei erneut doppelklicken.
echo.
pause
exit /b 1

:python_ok
REM 2) Virtuelle Umgebung anlegen, falls noch nicht vorhanden
if exist ".venv\Scripts\python.exe" goto venv_ok
echo Erste Einrichtung: virtuelle Umgebung wird erstellt ...
%PYCMD% -m venv .venv
if errorlevel 1 goto venv_fehler
if not exist ".venv\Scripts\python.exe" goto venv_fehler

:venv_ok
REM 3) Pakete installieren, falls noch nicht geschehen
if exist ".venv\.installiert" goto starten
echo Benoetigte Pakete werden installiert (einmalig, Internet noetig, 1-2 Minuten) ...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto pakete_fehler
echo installiert> ".venv\.installiert"
echo Fertig eingerichtet.
goto starten

:pakete_fehler
echo.
echo   [!] Einige Pakete konnten nicht installiert werden.
echo   Bitte Internetverbindung pruefen. Beim naechsten Start wird es erneut versucht.
echo   Die App startet trotzdem; einzelne Funktionen (z. B. Bank) fehlen ggf. noch.
echo.

:starten
REM 4) App starten
".venv\Scripts\python.exe" run.py
pause
exit /b 0

:venv_fehler
echo.
echo   [!] Die Einrichtung ist fehlgeschlagen (virtuelle Umgebung).
echo   Bitte den Ordner ".venv" loeschen und die Datei erneut doppelklicken.
echo.
pause
exit /b 1
