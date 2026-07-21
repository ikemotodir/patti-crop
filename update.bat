@echo off
rem PATTI CROP updater - double click after downloading patti_crop*.zip
rem ASCII only on purpose (cmd garbles Japanese in UTF-8 bat files).
rem All Japanese messages live in update.ps1 (UTF-8 with BOM).
title PATTI CROP UPDATE
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"
exit /b %errorlevel%
