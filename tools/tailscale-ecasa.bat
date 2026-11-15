@echo off
REM eCasa - actions Tailscale (Windows). Lancer en administrateur si besoin.
REM Usage : tailscale-ecasa.bat {install^|uninstall^|up [nom]^|serve^|reset^|status}
setlocal enabledelayedexpansion
set "ACTION=%~1"
set "HOSTNAME=%~2"
if "%ACTION%"=="" set "ACTION=status"
if "%HOSTNAME%"=="" set "HOSTNAME=ecasa"
set "PORT=8780"
if not "%ECASA_PORT%"=="" set "PORT=%ECASA_PORT%"

if /I "%ACTION%"=="install" goto install
if /I "%ACTION%"=="uninstall" goto uninstall
if /I "%ACTION%"=="up" goto up
if /I "%ACTION%"=="serve" goto serve
if /I "%ACTION%"=="reset" goto reset
if /I "%ACTION%"=="status" goto status
echo Usage: %~nx0 {install^|uninstall^|up [nom]^|serve^|reset^|status}
exit /b 2

:install
winget install -e --id Tailscale.Tailscale --silent --accept-package-agreements --accept-source-agreements
goto end

:uninstall
winget uninstall -e --id Tailscale.Tailscale --silent
goto end

:up
tailscale up --hostname=%HOSTNAME% --accept-routes=false
goto end

:serve
tailscale serve --bg --https=443 --set-path=/ http://127.0.0.1:%PORT%
goto end

:reset
tailscale serve reset
goto end

:status
tailscale status
echo --- serve ---
tailscale serve status
goto end

:end
endlocal
