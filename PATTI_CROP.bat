@echo off
rem PATTI CROP - console launcher (for debugging). Normal use: the desktop shortcut.
title PATTI CROP
cd /d "%~dp0"
if exist "%~dp0python\python.exe" (
    "%~dp0python\python.exe" server.py
) else (
    python server.py
)
pause
