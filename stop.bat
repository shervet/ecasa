@echo off
rem Arrete MeCasa et les services lies (Windows).
cd /d "%~dp0"

echo Arret de MeCasa...
taskkill /F /FI "WINDOWTITLE eq MeCasa*" >nul 2>&1
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH 2^>nul ^| findstr /i "mecasa"') do taskkill /F /PID %%a >nul 2>&1
wmic process where "commandline like '%%mecasa.main%%'" call terminate >nul 2>&1

echo Arret de pm2...
where pm2 >nul 2>&1 && pm2 stop all >nul 2>&1

echo Arret des conteneurs MyElectricalData locaux...
for /f "tokens=*" %%c in ('docker ps --format "{{.Names}}" 2^>nul ^| findstr /i "electricaldata med"') do docker stop %%c >nul 2>&1

echo Arret termine.
pause