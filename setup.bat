@echo off
rem PATTI CROP first-time setup - double click me!
rem ASCII only on purpose (cmd garbles Japanese in UTF-8 bat files).
rem All Japanese messages live in setup.ps1 (UTF-8 with BOM).
title PATTI CROP SETUP
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
exit /b %errorlevel%
