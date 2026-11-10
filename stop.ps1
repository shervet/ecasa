# Arret de MeCasa et des services lies (Windows).
$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*mecasa.main*' }
foreach ($p in $procs) {
    Write-Host "Arret du processus $($p.ProcessId) ($($p.Name))"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

# pm2
if (Get-Command pm2 -ErrorAction SilentlyContinue) {
    Write-Host 'Arret de pm2...'
    pm2 stop all 2>$null
    pm2 kill 2>$null
}

# Node
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' } | ForEach-Object {
    Write-Host "Arret node.exe PID $($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

# Docker MED
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host 'Arret des conteneurs MyElectricalData...'
    docker ps --format '{{.Names}}' 2>$null | ForEach-Object {
        if ($_ -match 'electricaldata|med') {
            Write-Host "Arret conteneur $_"
            docker stop $_ 2>$null
        }
    }
}

Write-Host 'Arret termine.'
