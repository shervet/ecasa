@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage : %~nx0 ^<archive.zip^>
  exit /b 1
)

set "PYTHON_BIN="
where python >nul 2>nul && set "PYTHON_BIN=python"
if "%PYTHON_BIN%"=="" where py >nul 2>nul && set "PYTHON_BIN=py -3"
if "%PYTHON_BIN%"=="" (
  echo Erreur : Python introuvable. Installez Python 3.
  exit /b 1
)

%PYTHON_BIN% config_backup.py import "%~1"
endlocal
