@echo off
setlocal enabledelayedexpansion
title WorkBuddy2API Dashboard
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem  Locate a Python 3 interpreter (PATH -> common per-user installs).
rem  Content kept ASCII-only on purpose: cmd.exe reads .bat in the OEM codepage,
rem  so non-ASCII text here would be garbled.
rem ---------------------------------------------------------------------------
set "PYEXE="
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "%ProgramFiles%\Python313\python.exe" set "PYEXE=%ProgramFiles%\Python313\python.exe"
if not defined PYEXE if exist "%ProgramFiles%\Python312\python.exe" set "PYEXE=%ProgramFiles%\Python312\python.exe"
if not defined PYEXE for %%P in (python.exe) do if not defined PYEXE set "PYEXE=%%~$PATH:P"
if not defined PYEXE for %%P in (py.exe) do if not defined PYEXE set "PYEXE=%%~$PATH:P"

if not defined PYEXE (
  echo [ERROR] Python 3 not found.
  echo         Install Python 3, or set PYEXE in this file to your python.exe.
  echo.
  pause
  exit /b 1
)

if not defined WB2API_DIR set "WB2API_DIR=%~dp0..\workbuddy2api"
set "WB2API_DIR_SHOWN=%WB2API_DIR%"
if not exist "%WB2API_DIR%" set "WB2API_DIR_SHOWN=%WB2API_DIR%  (not found yet - will be created)"

echo ==========================================================
echo   WorkBuddy2API  Control Panel
echo ==========================================================
echo   Python  : !PYEXE!
echo   Gateway : %WB2API_DIR_SHOWN%
echo   URL     : http://127.0.0.1:7864
echo.
echo   An app window (Edge/Chrome --app) will open automatically.
echo   Gateway + panel start natively, no Docker needed.
echo   Close the app window OR this console to stop everything.
echo ==========================================================
echo.

"!PYEXE!" "%~dp0launcher.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [ERROR] launcher.py exited with code %RC%.
  pause
)
endlocal
