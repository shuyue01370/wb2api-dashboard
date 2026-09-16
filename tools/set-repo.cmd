@echo off
setlocal
cd /d "%~dp0.."

rem ---------------------------------------------------------------------------
rem  Replace the <owner>/<repo> placeholder in the docs.
rem  Usage:  tools\set-repo.cmd owner/repo
rem  Content kept ASCII-only on purpose: cmd.exe reads .cmd in the OEM codepage.
rem ---------------------------------------------------------------------------

if "%~1"=="" (
  echo Usage: tools\set-repo.cmd owner/repo
  echo   e.g. tools\set-repo.cmd shuyue01370/wb2api-dashboard
  exit /b 1
)

set "PYEXE="
for %%P in (python.exe) do if not defined PYEXE set "PYEXE=%%~$PATH:P"
if not defined PYEXE for %%P in (py.exe) do if not defined PYEXE set "PYEXE=%%~$PATH:P"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

if not defined PYEXE (
  echo [ERROR] Python 3 not found.
  echo         Either install Python 3, or run this from Git Bash instead:
  echo             bash tools/set-repo.sh owner/repo
  exit /b 1
)

"%PYEXE%" "%~dp0set_repo.py" %*
