@echo off
rem Arrete MeCasa et les services lies (Windows).
cd /d "%~dp0"

echo Arret de MeCasa...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%mecasa.main%'\" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%mecasa%'\" | Where-Object { $_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo Arret de pm2...
where pm2 >nul 2>&1
if not errorlevel 1 pm2 stop all >nul 2>&1
if not errorlevel 1 pm2 kill >nul 2>&1

echo Arret des conteneurs MyElectricalData locaux...
for /f "tokens=*" %%c in ('docker ps --format "{{.Names}}" 2^>nul') do (
  echo %%c | findstr /i "electricaldata med" >nul 2>&1 && docker stop %%c >nul 2>&1
)

echo Arret des processus Node...
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq node.exe" /FO CSV /NH 2^>nul') do taskkill /F /PID %%a >nul 2>&1

echo Arret termine.
pause