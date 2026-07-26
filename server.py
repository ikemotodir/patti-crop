# -*- coding: utf-8 -*-
"""
PATTI CROP - 動画クロップ&変換アプリ (ポータブル版)
ローカルHTTPサーバー。Python標準ライブラリのみ。
ファイル選択はブラウザ内で完結（OSダイアログ不使用）。
ffmpegは同梱せず、初回起動時に自動ダウンロードして ffmpeg/ に常駐させる。
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
APP_VERSION = "1.6.3"
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

# アプリ内自動更新の参照先
UPDATE_API_URL = "https://api.github.com/repos/ikemotodir/patti-crop/releases/latest"
UPDATE_ZIP_URL = "https://github.com/ikemotodir/patti-crop/releases/latest/download/patti_crop.zip"

# ffmpegは同梱しない。初回DLで ffmpeg/bin/ に常駐させる
FFMPEG_DIR = os.path.join(APP_DIR, "ffmpeg")
FFMPEG_BIN = os.path.join(FFMPEG_DIR, "bin")
FFMPEG = os.path.join(FFMPEG_BIN, "ffmpeg.exe")
FFPROBE = os.path.join(FFMPEG_BIN, "ffprobe.exe")

# ダウンロード元 (第一優先: GitHub CDNで安定 / フォールバック: gyan.dev)
FFMPEG_URLS = [
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
]

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts",
              ".m2ts", ".mxf", ".wmv", ".webm"}

# Windowsでコンソールウィンドウを出さない
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

jobs = {}  # job_id -> dict
jobs_lock = threading.Lock()
_job_counter = [0]
_nvenc_cache = [None]  # None=未判定, True/False

# ffmpeg自動ダウンロードの状態
ffmpeg_dl = {"state": "idle", "percent": 0, "message": ""}
_ffmpeg_dl_lock = threading.Lock()

# 「送る」で渡されたファイル (ブラウザが /api/pending で回収する)
pending_files = []
pending_lock = threading.Lock()

# アプリ内自動更新の状態
update_info = {"available": False, "latest": None}
update_state = {"state": "idle", "percent": 0, "message": ""}
_update_lock = threading.Lock()


def run_cmd(args, timeout=None):
    return subprocess.run(
        args, capture_output=True, timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------
# ffmpeg 自動ダウンロード
# ------------------------------------------------------------------
def ffmpeg_ready():
    return os.path.isfile(FFMPEG) and os.path.isfile(FFPROBE)


def _remote_size(url):
    try:
        r = run_cmd(["curl.exe", "-sIL", "--ssl-no-revoke", url], timeout=30)
        text = r.stdout.decode("utf-8", "replace")
        sizes = re.findall(r"(?i)content-length:\s*(\d+)", text)
        return int(sizes[-1]) if sizes else 0
    except Exception:  # noqa: BLE001
        return 0


def _extract_ffmpeg(zip_path):
    os.makedirs(FFMPEG_BIN, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        want = {}
        for n in z.namelist():
            base = n.replace("\\", "/").split("/")[-1].lower()
            if base in ("ffmpeg.exe", "ffprobe.exe") and base not in want:
                want[base] = n
        if "ffmpeg.exe" not in want or "ffprobe.exe" not in want:
            raise RuntimeError("zip内にffmpeg.exe/ffprobe.exeが見つかりません")
        for base, n in want.items():
            dst = os.path.join(FFMPEG_BIN, base)
            with z.open(n) as srcf, open(dst, "wb") as dstf:
                shutil.copyfileobj(srcf, dstf)


def _ffmpeg_dl_worker():
    tmp = os.path.join(tempfile.gettempdir(), "patti_crop_ffmpeg.zip")
    last_err = ""
    got = False
    for i, url in enumerate(FFMPEG_URLS):
        ffmpeg_dl["message"] = ("動画エンジンをダウンロードしています..." if i == 0
                                else "別のサーバーから再取得しています...")
        ffmpeg_dl["percent"] = 0
        total = _remote_size(url)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        try:
            proc = subprocess.Popen(
                ["curl.exe", "-L", "--ssl-no-revoke", "--fail",
                 "-C", "-",
                 "--connect-timeout", "30",
                 "--speed-time", "20", "--speed-limit", "20000",
                 "--retry", "3", "--retry-delay", "3",
                 "-o", tmp, url],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError:
            last_err = "curl.exe が見つかりません (Windows 10/11 標準の機能です)"
            break
        while proc.poll() is None:
            time.sleep(0.5)
            try:
                cur = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            except OSError:
                cur = 0
            if total > 0:
                ffmpeg_dl["percent"] = min(95, round(cur / total * 100, 1))
                ffmpeg_dl["message"] = f"動画エンジンをダウンロード中... {cur // 1000000}MB / {total // 1000000}MB"
            else:
                ffmpeg_dl["message"] = f"動画エンジンをダウンロード中... {cur // 1000000}MB"
        if proc.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1000000:
            got = True
            break
        try:
            last_err = proc.stderr.read().decode("utf-8", "replace")[-300:]
        except Exception:  # noqa: BLE001
            last_err = ""

    if not got:
        ffmpeg_dl.update(state="error", percent=0, message=(
            "動画エンジンのダウンロードに失敗しました。\n"
            "・インターネットに接続されているか確認してください\n"
            "・接続を確認したら、アプリをいったん閉じて開き直してください\n"
            f"（技術的な詳細: {last_err or '不明なエラー'}）"
        ))
        return

    ffmpeg_dl.update(percent=96, message="展開しています...")
    try:
        _extract_ffmpeg(tmp)
    except Exception as e:  # noqa: BLE001
        ffmpeg_dl.update(state="error", percent=0, message=(
            "動画エンジンの展開に失敗しました。\n"
            "・ディスクの空き容量を確認してください\n"
            "・アプリを閉じて開き直してください\n"
            f"（技術的な詳細: {e}）"
        ))
        return
    try:
        os.remove(tmp)
    except OSError:
        pass

    if ffmpeg_ready():
        ffmpeg_dl.update(state="ready", percent=100, message="準備完了！")
    else:
        ffmpeg_dl.update(state="error", percent=0, message=(
            "動画エンジンの配置に失敗しました。アプリを閉じて開き直してみてください。"
        ))


def start_ffmpeg_download():
    with _ffmpeg_dl_lock:
        if ffmpeg_ready():
            ffmpeg_dl.update(state="ready", percent=100, message="準備完了！")
            return
        if ffmpeg_dl["state"] == "downloading":
            return  # 既に実行中
        ffmpeg_dl.update(state="downloading", percent=0, message="動画エンジンを準備しています...")
        threading.Thread(target=_ffmpeg_dl_worker, daemon=True).start()


# ------------------------------------------------------------------
# アプリ内自動更新
# ------------------------------------------------------------------
def _parse_ver(s):
    nums = re.findall(r"\d+", str(s or ""))
    if not nums:
        return None
    return tuple(int(x) for x in nums[:3])


def _update_zip_url():
    # repo_url.txt があればそちらを優先 (update.ps1 と同じ挙動)
    try:
        p = os.path.join(APP_DIR, "repo_url.txt")
        with open(p, "r", encoding="utf-8") as f:
            u = f.read().strip()
        if u.startswith("http"):
            return u
    except OSError:
        pass
    return UPDATE_ZIP_URL


def _update_check_worker():
    # ネット未接続やAPI失敗時は何もしない (エラーで邪魔しない)
    try:
        req = urllib.request.Request(UPDATE_API_URL, headers={
            "User-Agent": "PATTI-CROP",
            "Accept": "application/vnd.github+json",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        tag = str(data.get("tag_name") or "")
        latest = _parse_ver(tag)
        local = _parse_ver(APP_VERSION)
        if latest and local and latest > local:
            update_info["latest"] = tag.lstrip("vV")
            update_info["available"] = True
    except Exception:  # noqa: BLE001
        pass


def _jobs_running():
    with jobs_lock:
        return any(j.get("state") == "running" for j in jobs.values())


# 更新適用スクリプト。サーバー終了を待って上書きコピーし、アプリを再起動する。
# config.json / server.pid / ffmpeg は絶対に上書き・削除しない。
_APPLY_PS = r"""
param([int]$SrvPid, [string]$Src, [string]$Dst, [string]$Work)
for ($i = 0; $i -lt 60; $i++) {
    if (-not (Get-Process -Id $SrvPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
$ff = Join-Path $Dst 'ffmpeg'
robocopy $Src $Dst /E /R:3 /W:1 /XF config.json server.pid /XD $ff | Out-Null
# 後片付けは「更新作業用フォルダそのもの」だけ。
# (以前は親フォルダを消しており、zip直下にserver.pyがある構成だと
#  TEMPフォルダ全体を消してしまう不具合があった)
try {
    if ($Work -and (Test-Path $Work) -and
        ((Split-Path -Leaf $Work) -eq 'patti_crop_appupdate')) {
        Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
    }
} catch {}
Start-Process 'wscript.exe' -ArgumentList ('"' + (Join-Path $Dst 'PattiCrop.vbs') + '" --no-browser')
exit 0
"""


def _update_worker():
    tmp_zip = os.path.join(tempfile.gettempdir(), "patti_crop_appupdate.zip")
    url = _update_zip_url()
    update_state.update(state="downloading", percent=0,
                        message="新しいバージョンをダウンロードしています...")
    total = _remote_size(url)
    try:
        if os.path.exists(tmp_zip):
            os.remove(tmp_zip)
    except OSError:
        pass
    try:
        proc = subprocess.Popen(
            ["curl.exe", "-L", "--ssl-no-revoke", "--fail",
             "--connect-timeout", "30",
             "--speed-time", "20", "--speed-limit", "20000",
             "--retry", "3", "--retry-delay", "3",
             "-o", tmp_zip, url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        update_state.update(state="error", percent=0,
                            message="更新に必要な curl.exe が見つかりませんでした。")
        return
    while proc.poll() is None:
        time.sleep(0.5)
        try:
            cur = os.path.getsize(tmp_zip) if os.path.exists(tmp_zip) else 0
        except OSError:
            cur = 0
        if total > 0:
            update_state["percent"] = min(90, round(cur / total * 90, 1))
            update_state["message"] = f"新しいバージョンをダウンロード中... {cur // 1000000}MB / {total // 1000000}MB"
        else:
            update_state["message"] = f"新しいバージョンをダウンロード中... {cur // 1000000}MB"
    if proc.returncode != 0 or not os.path.exists(tmp_zip) or os.path.getsize(tmp_zip) < 1000000:
        update_state.update(state="error", percent=0, message=(
            "更新のダウンロードに失敗しました。ネット接続を確認して、後でもう一度試してください。"
        ))
        return

    update_state.update(percent=92, message="更新を展開しています...")
    work = os.path.join(tempfile.gettempdir(), "patti_crop_appupdate")
    try:
        if os.path.isdir(work):
            shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        with zipfile.ZipFile(tmp_zip) as z:
            z.extractall(work)
    except Exception:  # noqa: BLE001
        update_state.update(state="error", percent=0,
                            message="更新ファイルの展開に失敗しました。後でもう一度試してください。")
        return
    try:
        os.remove(tmp_zip)
    except OSError:
        pass

    # server.py を含むフォルダを探す (zip直下 or 1階層下)
    src = None
    if os.path.isfile(os.path.join(work, "server.py")):
        src = work
    else:
        for name in os.listdir(work):
            cand = os.path.join(work, name)
            if os.path.isfile(os.path.join(cand, "server.py")):
                src = cand
                break
    if not src:
        update_state.update(state="error", percent=0,
                            message="更新ファイルの中身を確認できませんでした。")
        return

    # 適用直前の安全確認: 変換が動き出していたら中止
    if _jobs_running():
        update_state.update(state="error", percent=0,
                            message="変換の実行中は更新できません。変換が終わってからもう一度どうぞ。")
        return

    update_state.update(percent=96, message="更新を適用して再起動します...")
    ps1 = os.path.join(work, "apply_update.ps1")
    try:
        with open(ps1, "w", encoding="utf-8-sig") as f:
            f.write(_APPLY_PS)
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", ps1,
             "-SrvPid", str(os.getpid()), "-Src", src, "-Dst", APP_DIR,
             "-Work", work],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        update_state.update(state="error", percent=0,
                            message="更新の適用を開始できませんでした。")
        return
    update_state.update(state="restarting", percent=98,
                        message="再起動しています... そのままお待ちください")
    # 応答を返し終えるのを少し待ってからサーバーを終了 (適用スクリプトが引き継ぐ)
    threading.Timer(1.0, lambda: os._exit(0)).start()


def start_update():
    with _update_lock:
        if update_state["state"] in ("downloading", "restarting"):
            return True, ""
        if _jobs_running():
            return False, "変換の実行中は更新できません。変換が終わってからもう一度どうぞ。"
        update_state.update(state="downloading", percent=0, message="準備しています...")
        threading.Thread(target=_update_worker, daemon=True).start()
        return True, ""


# ------------------------------------------------------------------
# 起動時セルフチェック
#   ショートカット・「送る」が無ければ作成。
#   既にあっても別フォルダの古いアプリを指していれば、今起動したアプリに貼り直す
#   (zipを別の場所に展開して更新した場合でも、アイコンから最新版が開くようにするため)
# ------------------------------------------------------------------
_SELFCHECK_PS = r"""
param([string]$AppDir)
$vbs = Join-Path $AppDir 'PattiCrop.vbs'
$ico = Join-Path $AppDir 'patti_crop.ico'
if (-not (Test-Path $vbs)) { exit 0 }
$ws = New-Object -ComObject WScript.Shell
function Set-Link($linkPath) {
    $needs = $true
    if (Test-Path $linkPath) {
        try {
            $cur = $ws.CreateShortcut($linkPath)
            if ($cur.TargetPath -eq $vbs) { $needs = $false }
        } catch { }
    }
    if ($needs) {
        $sc = $ws.CreateShortcut($linkPath)
        $sc.TargetPath = $vbs
        $sc.WorkingDirectory = $AppDir
        $sc.IconLocation = $ico
        $sc.Description = 'PATTI CROP'
        $sc.Save()
    }
}
Set-Link (Join-Path ([Environment]::GetFolderPath('Desktop')) 'PATTI CROP.lnk')
Set-Link (Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'Microsoft\Windows\SendTo\PATTI CROP.lnk')
exit 0
"""


def _selfcheck_worker():
    # 失敗してもアプリ動作には影響させない。ウィンドウは一切出さない。
    try:
        full = _SELFCHECK_PS.replace(
            "param([string]$AppDir)",
            "$AppDir = '" + APP_DIR.replace("'", "''") + "'")
        enc = base64.b64encode(full.encode("utf-16-le")).decode("ascii")
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-EncodedCommand", enc],
            capture_output=True, timeout=60,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------
# フォルダブラウズ (ブラウザ内ファイル選択)
# ------------------------------------------------------------------
def known_folders():
    home = os.path.expanduser("~")
    specs = [
        ("デスクトップ", ["Desktop", os.path.join("OneDrive", "Desktop"),
                       os.path.join("OneDrive - Personal", "Desktop")]),
        ("ダウンロード", ["Downloads"]),
        ("ビデオ", ["Videos"]),
        ("ドキュメント", ["Documents", os.path.join("OneDrive", "Documents")]),
    ]
    out = []
    for label, subs in specs:
        for sub in subs:
            p = os.path.join(home, sub)
            if os.path.isdir(p):
                out.append({"name": label, "path": p})
                break
    # ドライブ直下も出しておく
    for drive in ("C:\\", "D:\\", "E:\\", "F:\\", "G:\\", "I:\\"):
        if os.path.isdir(drive):
            out.append({"name": drive, "path": drive})
    return out


def list_dir(path, dirs_only=False):
    entries = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        try:
            is_dir = os.path.isdir(full)
        except OSError:
            continue
        if is_dir:
            if name.startswith("$") or name.startswith("."):
                continue
            entries.append({"name": name, "is_dir": True})
        elif not dirs_only:
            ext = os.path.splitext(name)[1].lower()
            if ext in VIDEO_EXTS:
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                entries.append({"name": name, "is_dir": False, "size": size})
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


# ------------------------------------------------------------------
# 変換まわり
# ------------------------------------------------------------------
def nvenc_available():
    if _nvenc_cache[0] is None:
        try:
            r = run_cmd([
                FFMPEG, "-v", "error", "-f", "lavfi",
                "-i", "color=black:s=256x256:d=0.1",
                "-c:v", "h264_nvenc", "-f", "null", "-",
            ], timeout=30)
            _nvenc_cache[0] = (r.returncode == 0)
        except Exception:  # noqa: BLE001
            _nvenc_cache[0] = False
    return _nvenc_cache[0]


def probe_file(path):
    r = run_cmd([
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration,bit_rate,format_name",
        "-show_entries", "stream=index,codec_type,codec_name,profile,pix_fmt,width,height,r_frame_rate,bit_rate,channels,sample_rate",
        "-show_entries", "stream_side_data_list",
        "-of", "json", path,
    ], timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-500:])
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    video = None
    audio = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and video is None:
            video = s
        elif s.get("codec_type") == "audio" and audio is None:
            audio = s
    if video is None:
        raise RuntimeError("動画ストリームが見つかりません")

    fr = video.get("r_frame_rate", "0/1")
    try:
        num, den = fr.split("/")
        fps = float(num) / float(den) if float(den) else 0
    except (ValueError, ZeroDivisionError):
        fps = 0

    duration = float(data.get("format", {}).get("duration", 0) or 0)

    # 回転メタデータ (縦撮り動画)
    rotation = 0
    for sd in video.get("side_data_list", []):
        if "rotation" in sd:
            try:
                rotation = int(sd["rotation"])
            except (TypeError, ValueError):
                pass
    width = video.get("width")
    height = video.get("height")
    if abs(rotation) % 180 == 90:
        width, height = height, width

    pix_fmt = video.get("pix_fmt", "")
    vcodec = video.get("codec_name", "")
    acodec = audio.get("codec_name", "") if audio else None

    playable_pix = pix_fmt in ("yuv420p", "yuvj420p", "nv12")
    playable_v = vcodec in ("h264", "hevc") and playable_pix
    playable_a = acodec in (None, "aac", "mp3")
    needs_convert = not (playable_v and playable_a)

    reasons = []
    if vcodec not in ("h264", "hevc"):
        reasons.append(f"映像コーデック {vcodec} は非対応の可能性")
    elif not playable_pix:
        reasons.append(f"ピクセル形式 {pix_fmt} (10-bit/4:2:2系) はWindows・iPhoneのデコーダー非対応")
    if not playable_a:
        reasons.append(f"音声 {acodec} (非圧縮PCM等) はMP4プレイヤー非対応の可能性")

    return {
        "path": path,
        "name": os.path.basename(path),
        "size": os.path.getsize(path),
        "duration": duration,
        "width": width,
        "height": height,
        "rotation": rotation,
        "fps": round(fps, 3),
        "vcodec": vcodec,
        "profile": video.get("profile", ""),
        "pix_fmt": pix_fmt,
        "acodec": acodec,
        "needs_convert": needs_convert,
        "reasons": reasons,
    }


def extract_frame(path, t, width=960):
    args = [
        FFMPEG, "-v", "error",
        "-ss", f"{max(0.0, t):.3f}", "-i", path,
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-",
    ]
    r = run_cmd(args, timeout=60)
    if r.returncode != 0 or not r.stdout:
        args[3] = f"{max(0.0, t - 0.5):.3f}"
        r = run_cmd(args, timeout=60)
        if r.returncode != 0 or not r.stdout:
            raise RuntimeError("フレーム抽出に失敗しました")
    return r.stdout


def unique_out_path(src_path, cropped, out_dir=None):
    folder = out_dir if (out_dir and os.path.isdir(out_dir)) else os.path.dirname(src_path)
    name = os.path.splitext(os.path.basename(src_path))[0]
    suffix = "_crop" if cropped else "_conv"
    out = os.path.join(folder, f"{name}{suffix}.mp4")
    n = 2
    while os.path.exists(out):
        out = os.path.join(folder, f"{name}{suffix}{n}.mp4")
        n += 1
    return out


def build_ffmpeg_args(src, out, crop, quality, encoder, fps_limit, has_audio=True):
    """encoder: 'nvenc' or 'x264'"""
    vf = []
    if crop:
        w = crop["w"] // 2 * 2
        h = crop["h"] // 2 * 2
        x = max(0, crop["x"])
        y = max(0, crop["y"])
        vf.append(f"crop={w}:{h}:{x}:{y}")
    vf.append("format=yuv420p")

    args = [FFMPEG, "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
            "-i", src, "-vf", ",".join(vf)]

    if encoder == "nvenc":
        cq = "23" if quality == "high" else "28"
        args += ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
                 "-cq", cq, "-b:v", "0", "-profile:v", "high"]
    else:
        crf = "20" if quality == "high" else "24"
        args += ["-c:v", "libx264", "-preset", "fast", "-crf", crf,
                 "-profile:v", "high"]

    if fps_limit:
        args += ["-r", str(fps_limit)]

    if has_audio:
        args += ["-c:a", "aac", "-b:a", "192k"]
    args += ["-movflags", "+faststart", out]
    return args


def convert_worker(job_id):
    with jobs_lock:
        job = jobs[job_id]
    src = job["src"]
    crop = job["crop"]
    quality = job["quality"]
    duration = job["duration"] or 1

    out = unique_out_path(src, bool(crop), job.get("out_dir"))
    job["out"] = out

    encoders = ["nvenc", "x264"] if (job["use_gpu"] and nvenc_available()) else ["x264"]
    last_err = ""
    for encoder in encoders:
        job["encoder"] = encoder
        args = build_ffmpeg_args(src, out, crop, quality, encoder,
                                 job.get("fps_limit"), job["has_audio"])
        try:
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
            job["proc"] = proc
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        us = int(line.split("=")[1])
                        job["percent"] = min(99, us / 1_000_000 / duration * 100)
                    except ValueError:
                        pass
                if job.get("cancelled"):
                    proc.kill()
                    break
            proc.wait()
            if job.get("cancelled"):
                job["state"] = "cancelled"
                if os.path.exists(out):
                    try:
                        os.remove(out)
                    except OSError:
                        pass
                return
            if proc.returncode == 0:
                job["percent"] = 100
                job["state"] = "done"
                job["out_size"] = os.path.getsize(out)
                return
            last_err = proc.stderr.read().decode("utf-8", "replace")[-800:]
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
        if os.path.exists(out):
            try:
                os.remove(out)
            except OSError:
                pass
        job["percent"] = 0

    job["state"] = "error"
    job["error"] = last_err or "変換に失敗しました"


# ------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        q = urllib.parse.parse_qs(parsed.query)

        if route == "/":
            with open(os.path.join(APP_DIR, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif route == "/api/browse":
            mode = q.get("mode", ["files"])[0]
            path = q.get("path", [""])[0]
            cfg = load_config()
            if not path:
                path = cfg.get("browse_dir") or ""
            if not path or not os.path.isdir(path):
                kf = known_folders()
                path = kf[0]["path"] if kf else os.path.expanduser("~")
            path = os.path.abspath(path)
            try:
                entries = list_dir(path, dirs_only=(mode == "dir"))
            except OSError as e:  # noqa: BLE001
                self._json({"error": f"このフォルダは開けません: {e}"}, 400)
                return
            cfg["browse_dir"] = path
            save_config(cfg)
            parent = os.path.dirname(path)
            if parent == path:
                parent = None
            self._json({
                "path": path, "parent": parent, "entries": entries,
                "shortcuts": known_folders(),
            })

        elif route == "/api/pending":
            with pending_lock:
                items = list(pending_files)
                pending_files.clear()
            self._json({"paths": items})

        elif route == "/api/frame":
            if not ffmpeg_ready():
                self._json({"error": "動画エンジンの準備が完了していません"}, 503)
                return
            path = q.get("path", [""])[0]
            t = float(q.get("t", ["0"])[0])
            w = int(q.get("w", ["960"])[0])
            try:
                img = extract_frame(path, t, w)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(img)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(img)
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        elif route == "/api/caps":
            ready = ffmpeg_ready()
            self._json({
                "ffmpeg_ready": ready,
                "nvenc": nvenc_available() if ready else False,
                "version": APP_VERSION,
                "out_dir": load_config().get("out_dir"),
            })

        elif route == "/api/update-check":
            self._json({
                "available": update_info["available"],
                "latest": update_info["latest"],
                "current": APP_VERSION,
            })

        elif route == "/api/update-progress":
            self._json({
                "state": update_state["state"],
                "percent": update_state["percent"],
                "message": update_state["message"],
            })

        elif route == "/api/ffmpeg-progress":
            self._json({
                "state": ffmpeg_dl["state"],
                "percent": ffmpeg_dl["percent"],
                "message": ffmpeg_dl["message"],
                "ready": ffmpeg_ready(),
            })

        elif route == "/api/progress":
            job_id = q.get("id", [""])[0]
            with jobs_lock:
                job = jobs.get(job_id)
            if not job:
                self._json({"error": "job not found"}, 404)
                return
            self._json({
                "state": job["state"],
                "percent": round(job.get("percent", 0), 1),
                "encoder": job.get("encoder"),
                "out": job.get("out"),
                "out_size": job.get("out_size"),
                "error": job.get("error"),
            })
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        try:
            body = self._read_body()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "bad request"}, 400)
            return

        if route == "/api/ffmpeg-download":
            start_ffmpeg_download()
            self._json({"ok": True})

        elif route == "/api/update-start":
            ok, err = start_update()
            if ok:
                self._json({"ok": True})
            else:
                self._json({"error": err}, 409)

        elif route == "/api/sendto":
            p = (body.get("path") or "").strip().strip('"')
            if p and os.path.isfile(p):
                with pending_lock:
                    pending_files.append(p)
            self._json({"ok": True})

        elif route == "/api/set-out-dir":
            cfg = load_config()
            cfg["out_dir"] = body.get("out_dir")
            save_config(cfg)
            self._json({"ok": True})

        elif route == "/api/quit":
            self._json({"ok": True})
            threading.Timer(0.3, lambda: os._exit(0)).start()

        elif route == "/api/probe":
            if not ffmpeg_ready():
                self._json({"error": "動画エンジンの準備が完了していません"}, 503)
                return
            path = (body.get("path") or "").strip().strip('"')
            if not path or not os.path.isfile(path):
                self._json({"error": f"ファイルが見つかりません: {path}"}, 400)
                return
            try:
                self._json(probe_file(path))
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        elif route == "/api/convert":
            if not ffmpeg_ready():
                self._json({"error": "動画エンジンの準備が完了していません"}, 503)
                return
            src = body.get("path")
            if not src or not os.path.isfile(src):
                self._json({"error": "ファイルが見つかりません"}, 400)
                return
            _job_counter[0] += 1
            job_id = str(_job_counter[0])
            job = {
                "state": "running", "percent": 0,
                "src": src,
                "crop": body.get("crop"),
                "quality": body.get("quality", "high"),
                "use_gpu": bool(body.get("use_gpu", True)),
                "duration": float(body.get("duration") or 0),
                "has_audio": bool(body.get("has_audio", True)),
                "fps_limit": body.get("fps_limit"),
                "out_dir": load_config().get("out_dir"),
            }
            with jobs_lock:
                jobs[job_id] = job
            threading.Thread(target=convert_worker, args=(job_id,), daemon=True).start()
            self._json({"id": job_id})

        elif route == "/api/cancel":
            job_id = body.get("id")
            with jobs_lock:
                job = jobs.get(job_id)
            if job:
                job["cancelled"] = True
            self._json({"ok": True})

        elif route == "/api/open-folder":
            path = body.get("path", "")
            if path and os.path.exists(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)


class Server(ThreadingHTTPServer):
    # 二重起動時に確実にOSErrorが出るようSO_REUSEADDRを無効化
    allow_reuse_address = False


def _send_to_running(url, file_args):
    for fa in file_args:
        try:
            data = json.dumps({"path": fa}).encode("utf-8")
            req = urllib.request.Request(
                url + "api/sendto", data=data,
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:  # noqa: BLE001
            pass


def main():
    url = f"http://127.0.0.1:{PORT}/"
    # 「送る」で渡されたファイルパス (--で始まるフラグは除外)
    file_args = [a for a in sys.argv[1:] if not a.startswith("--")]

    # ポートバインド。
    #   通常起動 / 更新直後の再起動: ポート解放待ちのため長めに粘る(8回)。
    #   「送る」の2つ目起動: 既存インスタンスを即検出したいので短く(3回)。
    attempts = 3 if file_args else 8
    server = None
    for _ in range(attempts):
        try:
            server = Server(("127.0.0.1", PORT), Handler)
            break
        except OSError:
            time.sleep(0.4)
    if server is None:
        # 既に起動中: 送られたファイルを実行中インスタンスへ渡してブラウザを開く
        _send_to_running(url, file_args)
        webbrowser.open(url)
        return

    # 自分がサーバー: 「送る」ファイルをpendingへ
    for fa in file_args:
        if os.path.isfile(fa):
            with pending_lock:
                pending_files.append(fa)

    try:
        with open(os.path.join(APP_DIR, "server.pid"), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    if ffmpeg_ready():
        ffmpeg_dl.update(state="ready", percent=100, message="準備完了！")
    else:
        start_ffmpeg_download()

    # 起動時セルフチェックと更新確認 (どちらも失敗しても静かに通常起動)
    threading.Thread(target=_selfcheck_worker, daemon=True).start()
    threading.Thread(target=_update_check_worker, daemon=True).start()

    print(f"PATTI CROP v{APP_VERSION} 起動: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
