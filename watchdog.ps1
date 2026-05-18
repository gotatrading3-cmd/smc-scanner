# ============================================================
#  GOTA TRADING - Watchdog
#  Verifie que les 4 bots tournent. Relance les morts.
#  Tue les doublons. Lance par tache planifiee toutes les 3 min.
# ============================================================
$dir = "C:\Users\GOTA TRADING\.claude\trading-analysis"
$log = Join-Path $dir "watchdog.log"

function Write-WLog($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Add-Content -Path $log -Value $line -Encoding utf8
}

# Bot -> wrapper cmd
$bots = [ordered]@{
    "dashboard.py"     = "run_dashboard.cmd"
    "mt5_executor.py"  = "run_mt5_executor.cmd"
    "grid_executor.py" = "run_grid.cmd"
    "telegram_bot.py"  = "run_telegram_bot.cmd"
}

# Recense les process python du projet, indexes par script
$instances = @{}
foreach ($p in (Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue)) {
    $cl = $p.CommandLine
    if (-not $cl) { continue }
    foreach ($bot in $bots.Keys) {
        if ($cl -like "*$bot*") {
            if (-not $instances.ContainsKey($bot)) { $instances[$bot] = @() }
            $instances[$bot] += $p.ProcessId
        }
    }
}

$actions = 0
foreach ($bot in $bots.Keys) {
    $pids = @()
    if ($instances.ContainsKey($bot)) { $pids = $instances[$bot] }

    if ($pids.Count -eq 0) {
        # Bot mort -> relance
        Write-WLog "$bot DOWN -> redemarrage"
        Start-Process cmd.exe -ArgumentList "/c `"$dir\$($bots[$bot])`"" -WindowStyle Hidden
        $actions++
    }
    elseif ($pids.Count -gt 1) {
        # Doublons -> garde le plus ancien (PID le plus petit en general), tue le reste
        $keep = ($pids | Sort-Object)[0]
        foreach ($x in $pids) {
            if ($x -ne $keep) {
                Write-WLog "$bot DOUBLON PID $x -> kill"
                Stop-Process -Id $x -Force -ErrorAction SilentlyContinue
                $actions++
            }
        }
    }
}

if ($actions -eq 0) {
    Write-WLog "OK - 4 bots sains"
}
