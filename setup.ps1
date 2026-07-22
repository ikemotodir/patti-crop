# PATTI CROP セットアップ (ポータブル版)
# Python同梱のためインストール不要。ここではショートカット作成と起動だけ。
# ffmpegはアプリ初回起動時に自動ダウンロードされる。
$ErrorActionPreference = 'Continue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host '=============================================='
Write-Host '   PATTI CROP セットアップ'
Write-Host '=============================================='
Write-Host ''
Write-Host 'デスクトップにショートカットを作成しています...'
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

Write-Host '右クリック「送る」に PATTI CROP を登録しています...'
try {
    $sendTo = Join-Path $env:APPDATA 'Microsoft\Windows\SendTo'
    $ws2 = New-Object -ComObject WScript.Shell
    $sc2 = $ws2.CreateShortcut((Join-Path $sendTo 'PATTI CROP.lnk'))
    $sc2.TargetPath = (Join-Path $dir 'PattiCrop.vbs')
    $sc2.WorkingDirectory = $dir
    $sc2.IconLocation = (Join-Path $dir 'patti_crop.ico')
    $sc2.Description = 'PATTI CROP で動画を読み込む'
    $sc2.Save()
    Write-Host '  [OK] 「送る」登録完了（動画を右クリック → 送る → PATTI CROP）'
} catch {
    Write-Host '[注意] 「送る」への登録に失敗しました（アプリ本体の動作には影響ありません）。'
}

Write-Host ''
Write-Host '=============================================='
Write-Host '   セットアップ完了！'
Write-Host '=============================================='
Write-Host ''
Write-Host 'デスクトップの「PATTI CROP」から起動できます。'
Write-Host '※初回起動時だけ、動画エンジン(約80MB)の自動ダウンロードが走ります。'
Write-Host '  画面に進捗が出るので、そのままお待ちください。'
Write-Host ''
Write-Host 'このままアプリを起動します...'
Start-Process 'wscript.exe' ('"' + (Join-Path $dir 'PattiCrop.vbs') + '"')
Start-Sleep -Seconds 2
exit 0
