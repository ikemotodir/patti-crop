# -*- coding: utf-8 -*-
"""
PATTI CROP - 動画クロップ&変換アプリ (ポータブル版)
ローカルHTTPサーバー。Python標準ライブラリのみ。
ffmpegは同梱せず、初回起動時に自動ダウンロードして ffmpeg/ に常駐させる。
"""
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
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
APP_VERSION = "1.4.1"
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

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

# Windowsでコンソールウィンドウを出さない
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

jobs = {}  # job_id -> dict
jobs_lock = threading.Lock()
_job_counter = [0]
_nvenc_cache = [None]  # None=未判定, True/False

# ffmpeg自動ダウンロードの状態
ffmpeg_dl = {"state": "idle", "percent": 0, "message": ""}
_ffmpeg_dl_lock = threading.Lock()


def run_cmd(args, timeout=None):
    return subprocess.run(
        args, capture_output=True, timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


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
                 "-C", "-",                          # 途中から再開 (リトライ時)
                 "--connect-timeout", "30",
                 "--speed-time", "20", "--speed-limit", "20000",  # 20秒間20KB/s未満なら中断
                 "--retry", "3", "--retry-delay", "3",            # 失速/一時エラーは自動再試行
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


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


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

    # 回転メタデータ (縦撮り動画)。ffmpegはデコード時に自動回転するので、
    # UI・クロップ座標は回転後の表示サイズで扱う
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

    # Windows/iPhoneで再生できるかの簡易判定
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
        # 末尾フレーム付近で失敗した場合は少し手前を試す
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
        # NVENC失敗 → x264でリトライ
        if os.path.exists(out):
            try:
                os.remove(out)
            except OSError:
                pass
        job["percent"] = 0

    job["state"] = "error"
    job["error"] = last_err or "変換に失敗しました"


# ------------------------------------------------------------------
# ファイル選択ダイアログ (PowerShell標準ダイアログ。tkinter不使用)
# ------------------------------------------------------------------
_FILTER = ("動画ファイル|*.mp4;*.mov;*.m4v;*.avi;*.mkv;*.mts;*.m2ts;*.mxf;*.wmv;*.webm|"
           "すべてのファイル (*.*)|*.*")

# ダイアログを確実に最前面に出すための前置きスクリプト。
# TopMostのダミーフォームをオーナーにし、SetForegroundWindow等(P/Invoke)で
# 複数手段を併用してフォアグラウンド化する。これをオーナーにShowDialogを呼ぶ。
_PS_PREFIX = r'''[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
try {
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class PcFg {
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] static extern bool AttachThreadInput(uint a, uint b, bool f);
  [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f);
  [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] static extern void keybd_event(byte v, byte s, uint f, IntPtr e);
  public static void Force(IntPtr h) {
    try { keybd_event(0x12,0,0,IntPtr.Zero); keybd_event(0x12,0,2,IntPtr.Zero); } catch {}
    IntPtr fg = GetForegroundWindow();
    uint pp; uint ft = GetWindowThreadProcessId(fg, out pp);
    uint mt = GetCurrentThreadId();
    AttachThreadInput(mt, ft, true);
    SetWindowPos(h, new IntPtr(-1), 0, 0, 0, 0, 0x0003);
    ShowWindow(h, 5);
    BringWindowToTop(h);
    SetForegroundWindow(h);
    AttachThreadInput(mt, ft, false);
  }
}
"@
} catch {}
function Show-Front($dlg) {
  $owner = New-Object System.Windows.Forms.Form
  $owner.FormBorderStyle = 'None'
  $owner.ShowInTaskbar = $false
  $owner.TopMost = $true
  $owner.Size = New-Object System.Drawing.Size(1,1)
  $owner.StartPosition = 'Manual'
  $owner.Location = New-Object System.Drawing.Point(0,0)
  $owner.Add_Shown({ try { $owner.Activate(); [PcFg]::Force($owner.Handle) } catch {} })
  $owner.Show()
  [System.Windows.Forms.Application]::DoEvents()
  try { [PcFg]::Force($owner.Handle) } catch {}
  $r = $dlg.ShowDialog($owner)
  $owner.Close(); $owner.Dispose()
  return $r
}
'''


def _run_ps_dialog(ps_body):
    # PowerShellをSTAで起動しWindows Formsダイアログを開く。
    # 日本語を確実に扱うため一時.ps1(UTF-8 BOM)に書いて -File で実行、出力もUTF-8。
    script = _PS_PREFIX + ps_body
    fd, path = tempfile.mkstemp(suffix=".ps1")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(b"\xef\xbb\xbf")
            f.write(script.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", path],
            capture_output=True, timeout=600, creationflags=CREATE_NO_WINDOW,
        )
        return r.stdout.decode("utf-8", "replace")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def pick_file_dialog():
    ps = (
        "$d = New-Object System.Windows.Forms.OpenFileDialog\n"
        "$d.Title = '動画ファイルを選択'\n"
        f"$d.Filter = '{_FILTER}'\n"
        "$r = Show-Front $d\n"
        "if ($r -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.FileName) }\n"
    )
    return _run_ps_dialog(ps).strip()


def pick_files_dialog():
    ps = (
        "$d = New-Object System.Windows.Forms.OpenFileDialog\n"
        "$d.Title = '動画ファイルを選択 (複数選択OK)'\n"
        "$d.Multiselect = $true\n"
        f"$d.Filter = '{_FILTER}'\n"
        "$r = Show-Front $d\n"
        "if ($r -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write(($d.FileNames -join \"`n\")) }\n"
    )
    out = _run_ps_dialog(ps).strip()
    return [p for p in out.split("\n") if p.strip()]


def pick_dir_dialog():
    ps = (
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog\n"
        "$d.Description = '出力先フォルダを選択'\n"
        "$r = Show-Front $d\n"
        "if ($r -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.SelectedPath) }\n"
    )
    return _run_ps_dialog(ps).strip()


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

        elif route == "/api/pick":
            try:
                path = pick_file_dialog()
                self._json({"path": path})
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        elif route == "/api/pick-multi":
            try:
                self._json({"paths": pick_files_dialog()})
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        elif route == "/api/pick-dir":
            try:
                path = pick_dir_dialog()
                if path:
                    cfg = load_config()
                    cfg["out_dir"] = path
                    save_config(cfg)
                self._json({"path": path})
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        elif route == "/api/set-out-dir":
            # out_dir: null = 元ファイルと同じフォルダ
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
    # WindowsではSO_REUSEADDRだと同一ポートに二重バインドできてしまうため無効化
    # (これで二重起動時に確実にOSErrorが出る)
    allow_reuse_address = False


def main():
    url = f"http://127.0.0.1:{PORT}/"
    # ポートバインドを数秒リトライする。
    # ・アップデート直後の再起動: 旧プロセスのポート解放待ちを吸収して確実に起動
    # ・本当に二重起動: 数秒粘っても空かない → 既存インスタンスとみなしブラウザだけ開く
    server = None
    for _ in range(8):
        try:
            server = Server(("127.0.0.1", PORT), Handler)
            break
        except OSError:
            time.sleep(0.5)
    if server is None:
        webbrowser.open(url)
        return
    # アップデーターが確実にプロセスを特定できるようPIDを記録
    try:
        with open(os.path.join(APP_DIR, "server.pid"), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    # ffmpegが未取得なら起動と同時にダウンロード開始 (UIも進捗を表示する)
    if ffmpeg_ready():
        ffmpeg_dl.update(state="ready", percent=100, message="準備完了！")
    else:
        start_ffmpeg_download()
    print(f"PATTI CROP v{APP_VERSION} 起動: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
