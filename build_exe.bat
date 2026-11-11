@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo   Blue Star AC - build BlueStarAC.exe
echo ============================================
echo.

REM ---- 1. both source files must be here, and be the new versions
if not exist bluestar_app.py goto :missing_app
if not exist bluestar_pc.py goto :missing_pc
findstr /C:"CORE_VERSION = 3" bluestar_pc.py >nul
if errorlevel 1 goto :old_pc

REM ---- 2. find Python
set PY=
where py >nul 2>nul
if not errorlevel 1 set PY=py
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set PY=python
)
if not defined PY goto :no_python

REM ---- 3. tkinter (the window toolkit) must be installed
%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 goto :no_tk

REM ---- 4. clean leftovers from earlier builds
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist BlueStarAC.spec del /q BlueStarAC.spec

REM ---- 5. build in a private virtual environment
if not exist venv\Scripts\activate.bat (
    echo Creating virtual environment...
    %PY% -m venv venv
    if errorlevel 1 goto :fail
)
call venv\Scripts\activate.bat
if errorlevel 1 goto :fail

echo Installing build tools...
python -m pip install --upgrade pip >nul
pip install requests paho-mqtt pyinstaller
if errorlevel 1 goto :fail

echo.
echo Building the app (this takes a minute or two)...
pyinstaller --onefile --noconsole --clean --name BlueStarAC bluestar_app.py
if errorlevel 1 goto :fail

if not exist dist\BlueStarAC.exe goto :fail
echo.
echo ============================================
echo   Done!  Your app is:  dist\BlueStarAC.exe
echo ============================================
echo You can copy that single file to any Windows PC.
explorer dist
goto :end

:missing_app
echo ERROR: bluestar_app.py is not in this folder.
goto :fail_quiet
:missing_pc
echo ERROR: bluestar_pc.py is not in this folder. It must sit next to bluestar_app.py.
goto :fail_quiet
:old_pc
echo ERROR: bluestar_pc.py is an OLD version. Replace it with the newest one
echo        (it contains the line:  CORE_VERSION = 3 ).
goto :fail_quiet
:no_python
echo ERROR: Python was not found. Install Python 3.9 or newer from python.org
echo        and tick "Add python.exe to PATH" during setup.
goto :fail_quiet
:no_tk
echo ERROR: Python is installed but without tkinter. Run the Python installer again,
echo        choose Modify, and make sure "tcl/tk and IDLE" is ticked.
goto :fail_quiet
:fail
echo.
echo The build failed. Scroll up to see the first error message.
:fail_quiet
echo.
:end
pause
endlocal
