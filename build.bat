@echo off
rem Builds a single windowed exe: dist\MiniBro.exe
pip install -r requirements.txt pyinstaller || exit /b 1
pyinstaller --onefile --noconsole --name MiniBro --collect-all tkinterdnd2 minibro\main.py
