# PATTI CROP パブリッシャ — GitHub Release を作成する
# 安全装置: 確認プロンプト / 同タグは上書きせず中止 / 削除系コマンド不使用 / 失敗時は日本語で停止 / ログ記録
$ErrorActionPreference = 'Continue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = 'ikemotodir/patti-crop'
$log = Join-Path $dir 'publish_log.txt'

function Log($m) {
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    # ログは補助情報。ウイルス対策ソフト等で一時的にロックされても
    # 公開処理そのものは止めない (画面表示は必ず出す)
    $line = '[' + $ts + '] ' + $m
    foreach ($i in 1..3) {
        try {
            $sw = [System.IO.StreamWriter]::new($log, $true, [System.Text.UTF8Encoding]::new($false))
            $sw.WriteLine($line); $sw.Close()
            break
        } catch { Start-Sleep -Milliseconds 120 }
    }
    Write-Host $m
}
function Die($m) {
    Log ('[エラー] ' + $m)
    Write-Host ''
    [void](Read-Host 'Enterキーを押すと閉じます')
    exit 1
}

function Build-ReleaseZip($srcDir) {
    Add-Type -AssemblyName System.IO.Compression | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
    # .git は利用者に不要（配布zipに入れると肥大化するので必ず除外）
    $ex = @('config.json', 'server.pid', 'ffmpeg', '__pycache__', 'publish_log.txt', '.git')
    $stageRoot = Join-Path $env:TEMP ('pcz_' + [System.IO.Path]::GetRandomFileName())
    $stage = Join-Path $stageRoot 'src'
    New-Item -ItemType Directory -Force $stage | Out-Null
    Get-ChildItem $srcDir -Force | Where-Object { $ex -notcontains $_.Name } | ForEach-Object {
        Copy-Item $_.FullName -Destination $stage -Recurse -Force
    }
    $zipDir = Join-Path $stageRoot 'out'
    New-Item -ItemType Directory -Force $zipDir | Out-Null
    $zipPath = Join-Path $zipDir 'patti_crop.zip'
    $fs = [System.IO.File]::Open($zipPath, [System.IO.FileMode]::Create)
    $arch = [System.IO.Compression.ZipArchive]::new($fs, [System.IO.Compression.ZipArchiveMode]::Create)
    $base = (Resolve-Path $stage).Path.TrimEnd('\') + '\'
    foreach ($f in (Get-ChildItem $stage -Recurse -File -Force)) {
        $rel = $f.FullName.Substring($base.Length).Replace('\', '/')
        $e = $arch.CreateEntry($rel, [System.IO.Compression.CompressionLevel]::Optimal)
        $o = $e.Open(); $by = [System.IO.File]::ReadAllBytes($f.FullName); $o.Write($by, 0, $by.Length); $o.Dispose()
    }
    $arch.Dispose(); $fs.Dispose()
    return $zipPath
}

function Copy-Source($srcDir, $cloneDir) {
    $ex = @('python', 'ffmpeg', 'config.json', 'server.pid', '__pycache__', '.git', 'publish_log.txt')
    Get-ChildItem $srcDir -Force | Where-Object { $ex -notcontains $_.Name } | ForEach-Object {
        Copy-Item $_.FullName -Destination $cloneDir -Recurse -Force
    }
}

Log '=============================================='
Log '   PATTI CROP パブリッシュ'
Log '=============================================='

# gh の場所を解決 (PATH → Program Files)
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) {
    $cand = Join-Path $env:ProgramFiles 'GitHub CLI\gh.exe'
    if (Test-Path $cand) { $gh = $cand }
}
if (-not $gh) { Die 'GitHub CLI (gh) が見つかりません。インストールを確認してください。' }

# バージョン取得
$svPath = Join-Path $dir 'server.py'
$m = [regex]::Match([System.IO.File]::ReadAllText($svPath, [System.Text.Encoding]::UTF8), 'APP_VERSION\s*=\s*"([^"]+)"')
if (-not $m.Success) { Die 'server.py からバージョンを取得できませんでした。' }
$ver = $m.Groups[1].Value
$tag = 'v' + $ver
Log ('対象バージョン: ' + $tag)

# git / gh認証
& git --version *> $null
if ($LASTEXITCODE -ne 0) { Die 'git が見つかりません。' }
& $gh auth status 2> $null | Out-Null
if ($LASTEXITCODE -ne 0) { Die 'GitHub CLI の認証が確認できません。gh auth login を済ませてください。' }

# 確認プロンプト
if ($env:PATTI_PUBLISH_YES -ne '1') {
    $ans = Read-Host ('バージョン ' + $tag + ' を公開します。よろしいですか？(Y/N)')
    if ($ans -notmatch '^[Yy]') { Log 'ユーザーが中止しました。'; exit 0 }
}

# 同タグ存在チェック（あれば上書きせず中止）
& $gh release view $tag -R $repo 2> $null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Die ($tag + ' は既に公開済みです。上書きせず中止しました。server.py の APP_VERSION を上げてください。')
}

# リリースzip作成
Log 'リリース用zip (patti_crop.zip) を作成しています...'
try { $zip = Build-ReleaseZip $dir } catch { Die ('zip作成に失敗しました: ' + $_.Exception.Message) }
Log ('  作成: ' + $zip)

# git 認証ヘルパをghに設定（pushを確実にする）
& $gh auth setup-git 2> $null | Out-Null

# ソース更新（クローンにオーバーレイして push）
Log 'リポジトリのソースを更新しています...'
$work = Join-Path $env:TEMP ('pcpub_' + $tag)
if (-not (Test-Path (Join-Path $work '.git'))) {
    & $gh repo clone $repo "$work" 2> $null
    if ($LASTEXITCODE -ne 0) { Die 'リポジトリのクローンに失敗しました。' }
} else {
    & git -C "$work" pull 2> $null | Out-Null
}
Copy-Source $dir $work
& git -C "$work" config user.name 'PATTI CROP Publisher' | Out-Null
& git -C "$work" config user.email 'ikemotodir@users.noreply.github.com' | Out-Null
& git -C "$work" add -A
& git -C "$work" commit -m $tag 2> $null | Out-Null   # 変更なしなら非0だが問題なし
& git -C "$work" push
if ($LASTEXITCODE -ne 0) { Die 'ソースの push に失敗しました（ネットワークか権限を確認してください）。' }
Log '  ソース更新 完了'

# Release作成 + zip添付（資産名は patti_crop.zip 固定）
Log 'GitHub Release を作成しています...'
& $gh release create $tag "$zip" -R $repo --title $tag --notes ('PATTI CROP ' + $tag)
if ($LASTEXITCODE -ne 0) { Die 'Release の作成に失敗しました。' }

# リリースURL表示
$rurl = & $gh release view $tag -R $repo --json url -q .url
Log ''
Log '=============================================='
Log ('   公開完了！  ' + $tag)
Log ('   ' + $rurl)
Log '=============================================='
Write-Host ''
[void](Read-Host 'Enterキーを押すと閉じます')
exit 0
