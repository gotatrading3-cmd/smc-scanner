# Stop le scanner SMC (kill les python.exe lancant scanner.py).

$found = $false
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | ForEach-Object {
    if ($_.CommandLine -and $_.CommandLine -like "*scanner.py*") {
        Write-Host "Kill PID $($_.ProcessId) : $($_.CommandLine)"
        Stop-Process -Id $_.ProcessId -Force
        $found = $true
    }
}
if (-not $found) {
    Write-Host "Aucun scanner en cours."
}

# Optionnel : desactive aussi la tache pour ne pas redemarrer au prochain login
$task = Get-ScheduledTask -TaskName "GotaTrading-SmcScanner" -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "Tache planifiee 'GotaTrading-SmcScanner' : $($task.State)"
    Write-Host "Pour la desactiver : Disable-ScheduledTask -TaskName 'GotaTrading-SmcScanner'"
}
