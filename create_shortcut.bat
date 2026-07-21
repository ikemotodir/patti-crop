@echo off
rem PATTI CROP - create desktop shortcut (ASCII only: cmd garbles Japanese in UTF-8)
cd /d "%~dp0"
title PATTI CROP - create shortcut
echo Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\PATTI CROP.lnk'); $sc.TargetPath = '%~dp0PattiCrop.vbs'; $sc.WorkingDirectory = '%~dp0'; $sc.IconLocation = '%~dp0patti_crop.ico'; $sc.Save()"
if errorlevel 1 (
    echo [ERROR] Failed to create shortcut.
    pause
    exit /b 1
)
echo Done! Launch PATTI CROP from the desktop icon.
echo (No console window will appear. Quit from the button in the app.)
pause
