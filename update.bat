@echo off
rem PATTI CROP updater - just double click me.
rem Downloads the latest release from GitHub automatically.
rem (Falls back to a patti*crop*.zip in your Downloads folder if that fails.)
rem ASCII only on purpose (cmd garbles Japanese in UTF-8 bat files).
rem All Japanese messages live in update.ps1 (UTF-8 with BOM).
title PATTI CROP UPDATE
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"
exit /b %errorlevel%
