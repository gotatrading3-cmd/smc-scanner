# Wrapper du scanner SMC : log fichier + working dir + PYTHONPATH explicite.
# Appele par la tache planifiee Windows.

$ErrorActionPreference = "Continue"
$dir    = Join-Path $env:USERPROFILE ".claude\trading-analysis"
$python = Join-Path $env:LocalAppData "Programs\Python\Python312\python.exe"
$log    = Join-Path $dir "scanner.log"

# Force le user site-packages (au cas ou le task scheduler ne l'initialise pas)
$userSite = Join-Path $env:APPDATA "Python\Python312\site-packages"
$env:PYTHONPATH = $userSite
$env:PYTHONIOENCODING = "utf-8"

Set-Location $dir

# Rotation simple : si log > 5 MB, archive
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5MB)) {
    Move-Item $log (Join-Path $dir "scanner.log.old") -Force
}

# Helper : ecrit dans le log en UTF-8 sans BOM
function Write-Log {
    param([string]$Message)
    [System.IO.File]::AppendAllText($log, "$Message`r`n", [System.Text.UTF8Encoding]::new($false))
}

Write-Log ""
Write-Log "=== Scanner demarrage $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Write-Log "  Python     : $python"
Write-Log "  USERPROFILE: $env:USERPROFILE"
Write-Log "  APPDATA    : $env:APPDATA"
Write-Log "  PYTHONPATH : $env:PYTHONPATH"
Write-Log "  Working dir: $((Get-Location).Path)"
Write-Log ""

# Lance python en redirigeant tous les flux dans un fichier temporaire,
# puis on l'append au log principal en UTF-8 propre.
$tmpOut = Join-Path $dir "scanner.tmp.log"
$proc = Start-Process -FilePath $python `
    -ArgumentList @("$dir\scanner.py") `
    -RedirectStandardOutput $tmpOut `
    -RedirectStandardError "$tmpOut.err" `
    -NoNewWindow -PassThru

$proc.WaitForExit()

if (Test-Path $tmpOut) {
    Get-Content $tmpOut -Encoding utf8 -ErrorAction SilentlyContinue | ForEach-Object { Write-Log $_ }
    Remove-Item $tmpOut -Force -ErrorAction SilentlyContinue
}
if (Test-Path "$tmpOut.err") {
    Get-Content "$tmpOut.err" -Encoding utf8 -ErrorAction SilentlyContinue | ForEach-Object { Write-Log "[stderr] $_" }
    Remove-Item "$tmpOut.err" -Force -ErrorAction SilentlyContinue
}

Write-Log "=== Scanner arrete   $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') (exit code $($proc.ExitCode)) ==="
