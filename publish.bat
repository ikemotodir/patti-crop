@echo off
rem PATTI CROP publisher - double click to create a GitHub Release.
rem ASCII only on purpose (cmd garbles Japanese in UTF-8 bat files).
rem All Japanese messages and logic live in publish.ps1 (UTF-8 with BOM).
title PATTI CROP PUBLISH
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish.ps1"
exit /b %errorlevel%
