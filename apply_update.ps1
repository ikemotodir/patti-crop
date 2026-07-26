
param([int]$SrvPid, [string]$Src, [string]$Dst)
for ($i = 0; $i -lt 60; $i++) {
    if (-not (Get-Process -Id $SrvPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
$ff = Join-Path $Dst 'ffmpeg'
robocopy $Src $Dst /E /R:3 /W:1 /XF config.json server.pid /XD $ff | Out-Null
try { Remove-Item (Split-Path -Parent $Src) -Recurse -Force -ErrorAction SilentlyContinue } catch {}
Start-Process 'wscript.exe' -ArgumentList ('"' + (Join-Path $Dst 'PattiCrop.vbs') + '" --no-browser')
exit 0
