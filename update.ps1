# PATTI CROP アップデーター
# GitHubの最新リリース(固定URL)から取得して適用する。
# 取得に失敗した場合のみ、ダウンロードフォルダ内の patti*crop*.zip を探すフォールバック。
$ErrorActionPreference = 'Stop'
function Fin($c) { [void](Read-Host 'Enterキーを押すと閉じます'); exit $c }

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 参照先の固定URL(バージョンが上がっても不変)。repo_url.txt があればそちらを優先。
$updateUrl = 'https://github.com/ikemotodir/patti-crop/releases/latest/download/patti_crop.zip'
$repoUrlFile = Join-Path $dir 'repo_url.txt'
if (Test-Path $repoUrlFile) {
    $u = (Get-Content $repoUrlFile -Raw).Trim()
    if ($u -match '^https?://') { $updateUrl = $u }
}

Write-Host '=============================================='
Write-Host '   PATTI CROP アップデート'
Write-Host '=============================================='
Write-Host ''

# (1/4) 最新版zipを入手 --- 第一優先: GitHub固定URL / フォールバック: Downloadsフォルダ
$zipPath = $null
$fromDownloads = $false

Write-Host '(1/4) GitHubから最新版をダウンロードしています...'
$tmpZip = Join-Path $env:TEMP 'patti_crop_latest.zip'
try { if (Test-Path $tmpZip) { Remove-Item $tmpZip -Force } } catch {}
# --ssl-no-revoke: 一部環境でTLS失効チェックが通らずDLに失敗するのを回避
cmd /c "curl.exe -L --ssl-no-revoke --fail -s -S -o `"$tmpZip`" `"$updateUrl`" 2>nul"
if ($LASTEXITCODE -eq 0 -and (Test-Path $tmpZip) -and ((Get-Item $tmpZip).Length -gt 0)) {
    $zipPath = $tmpZip
    Write-Host '  [OK] ダウンロード完了'
} else {
    Write-Host '  GitHubから取得できませんでした → ダウンロードフォルダのzipを探します'
}

if ($null -eq $zipPath) {
    $downloads = Join-Path $env:USERPROFILE 'Downloads'
    $zip = Get-ChildItem -Path (Join-Path $downloads 'patti*crop*.zip') -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -eq $zip) {
        Write-Host ''
        Write-Host '[エラー] アップデート用のzipが見つかりませんでした。'
        Write-Host 'ネット接続を確認するか、配布ページのzipをダウンロードフォルダに入れて、もう一度実行してね。'
        Fin 1
    }
    $zipPath = $zip.FullName
    $fromDownloads = $true
    Write-Host ('  ダウンロードフォルダで発見: ' + $zip.Name)
}

# (2/4) 起動中のPATTI CROPを終了 (server.pid のPIDで確実に特定)
Write-Host '(2/4) 起動中のPATTI CROPを終了しています...'
$pidFile = Join-Path $dir 'server.pid'
if (Test-Path $pidFile) {
    try {
        $srvPid = [int](Get-Content $pidFile -Raw).Trim()
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $srvPid" -ErrorAction Stop
        if ($proc -and $proc.Name -match '^pythonw?\.exe$' -and $proc.CommandLine -like '*server.py*') {
            Stop-Process -Id $srvPid -Force
        }
    } catch {}
    try { Remove-Item $pidFile -Force } catch {}
}
Start-Sleep -Milliseconds 500

# (3/4) 展開
Write-Host '(3/4) 展開しています...'
$work = Join-Path $env:TEMP 'patti-crop-update'
try { if (Test-Path $work) { Remove-Item $work -Recurse -Force } } catch {}
New-Item -ItemType Directory -Force $work | Out-Null
try { Expand-Archive -Path $zipPath -DestinationPath $work -Force } catch {
    Write-Host '[エラー] zipの展開に失敗しました。ファイルが壊れているかも。'
    Fin 1
}
# server.py を含むフォルダを探す (zip直下 or 1階層下)
$src = $null
if (Test-Path (Join-Path $work 'server.py')) { $src = $work }
else {
    $sub = Get-ChildItem $work -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'server.py') } | Select-Object -First 1
    if ($sub) { $src = $sub.FullName }
}
if ($null -eq $src) {
    Write-Host '[エラー] zipの中に server.py が見つかりませんでした。PATTI CROP用のzipか確認してね。'
    Fin 1
}

# (4/4) 上書きコピー (設定ファイルと実行中のアップデーターは保護)
Write-Host '(4/4) ファイルを更新しています...'
robocopy $src $dir /E /XF update.bat config.json server.pid | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Host '[エラー] ファイルの上書きに失敗しました。'
    Fin 1
}
try { Remove-Item $work -Recurse -Force } catch {}
if ($fromDownloads) {
    try { Remove-Item $zipPath -Force; Write-Host ('  使用済みzipを削除: ' + (Split-Path $zipPath -Leaf)) } catch {}
} else {
    try { Remove-Item $zipPath -Force } catch {}
}

# 新バージョンを表示
$ver = '(不明)'
try {
    $m = [regex]::Match((Get-Content (Join-Path $dir 'server.py') -Raw -Encoding UTF8), 'APP_VERSION\s*=\s*"([^"]+)"')
    if ($m.Success) { $ver = 'v' + $m.Groups[1].Value }
} catch {}
Write-Host ''
Write-Host '=============================================='
Write-Host ('   更新完了！ バージョン: ' + $ver)
Write-Host '=============================================='
Write-Host ''
Write-Host 'PATTI CROPを起動します...'
Start-Process 'wscript.exe' ('"' + (Join-Path $dir 'PattiCrop.vbs') + '"')
Start-Sleep -Seconds 2
exit 0
