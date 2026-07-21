# PATTI CROP 初回セットアップ
$ErrorActionPreference = 'Continue'
function Fin($c) { [void](Read-Host 'Enterキーを押すと閉じます'); exit $c }

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host '=============================================='
Write-Host '   PATTI CROP 初回セットアップ'
Write-Host '=============================================='
Write-Host ''

# (1/3) Python チェック
Write-Host '(1/3) Pythonを確認しています...'
$py = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $py) {
    Write-Host ''
    Write-Host '[エラー] Pythonが見つかりません。'
    Write-Host 'https://www.python.org/downloads/ からインストールしてください。'
    Write-Host '※インストール時に「Add python.exe to PATH」に必ずチェックを入れること！'
    Fin 1
}
$ver = & python --version
Write-Host ('  [OK] ' + $ver)
& python -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '[注意] tkinterが見つかりません。ファイル選択ダイアログが使えない可能性があります。'
    Write-Host 'Python公式インストーラーで「tcl/tk and IDLE」を含めて再インストールすると直ります。'
}

# (2/3) ffmpeg チェック (なければwingetでインストール)
Write-Host '(2/3) ffmpegを確認しています...'
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($null -eq $ff) {
    Write-Host '  ffmpegが見つかりません。自動インストールを試みます (数分かかります)...'
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    # インストール直後はPATHが未反映なのでレジストリから読み直す
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = $machinePath + ';' + $userPath
    $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($null -eq $ff) {
        Write-Host ''
        Write-Host '[注意] ffmpegのインストールを確認できませんでした。'
        Write-Host 'PCを再起動してからもう一度 setup.bat を実行してみてください。'
        Write-Host '(それでもダメなら https://www.gyan.dev/ffmpeg/builds/ から手動インストール)'
        Fin 1
    }
}
Write-Host '  [OK] ffmpeg検出'

# (3/3) デスクトップショートカット作成
Write-Host '(3/3) デスクトップにショートカットを作成しています...'
try {
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\PATTI CROP.lnk')
    $sc.TargetPath = (Join-Path $dir 'PattiCrop.vbs')
    $sc.WorkingDirectory = $dir
    $sc.IconLocation = (Join-Path $dir 'patti_crop.ico')
    $sc.Description = 'PATTI CROP - 動画クロップ&変換'
    $sc.Save()
    Write-Host '  [OK] ショートカット作成完了'
} catch {
    Write-Host '[注意] ショートカット作成に失敗しました。create_shortcut.bat を試してください。'
}

Write-Host ''
Write-Host '=============================================='
Write-Host '   セットアップ完了！'
Write-Host '=============================================='
Write-Host ''
Write-Host 'デスクトップの「PATTI CROP」から起動できます。'
Write-Host 'このままアプリを起動します...'
Start-Process 'wscript.exe' ('"' + (Join-Path $dir 'PattiCrop.vbs') + '"')
Start-Sleep -Seconds 2
exit 0
