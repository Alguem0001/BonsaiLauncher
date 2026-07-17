@echo off
cd /d "%~dp0"
python -m pip install -U pyinstaller huggingface_hub
python -m PyInstaller --noconfirm --onefile --windowed --name BonsaiLauncher ^
  --distpath dist --workpath build --specpath build ^
  bonsai_launcher.py
if errorlevel 1 exit /b 1
echo.
echo EXE: %~dp0dist\BonsaiLauncher.exe
copy /Y dist\BonsaiLauncher.exe "%~dp0BonsaiLauncher.exe"
echo Copied to %~dp0BonsaiLauncher.exe
