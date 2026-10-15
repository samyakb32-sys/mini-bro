@echo off
rem Builds dist\MiniBro.exe (the island) and dist\minibro-hook.exe (console relay used by agent hooks).
rem Keep both exes in the same folder.
pip install -r requirements.txt pyinstaller || exit /b 1
pyinstaller --onefile --noconsole --name MiniBro --collect-all tkinterdnd2 minibro\main.py || exit /b 1
pyinstaller --onefile --console --name minibro-hook minibro\hook.py
