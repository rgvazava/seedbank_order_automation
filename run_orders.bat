@echo off
setlocal

REM Always run from this script's folder (project root)
cd /d "%~dp0"

REM Paths
set "SCRIPT=DO_NOT_TOUCH\process_orders.py"
set "CONFIG=DO_NOT_TOUCH\config.json"

REM Ensure input folder exists
if not exist "inputs" mkdir "inputs"

REM Default expected input file (when user does NOT drag-drop)
set "EXPECTED_INPUT=inputs\SQL_GRIN_ORDER.csv"

REM If user drag-dropped a file, copy it into inputs with the correct extension
if "%~1"=="" goto NOFILE

echo Using input file: "%~1"
set "EXT=%~x1"
echo Detected extension: %EXT%

if /I "%EXT%"==".csv"  set "EXPECTED_INPUT=inputs\SQL_GRIN_ORDER.csv"
if /I "%EXT%"==".xlsx" set "EXPECTED_INPUT=inputs\SQL_GRIN_ORDER.xlsx"
if /I "%EXT%"==".xls"  set "EXPECTED_INPUT=inputs\SQL_GRIN_ORDER.xls"

REM Validate extension
if /I not "%EXT%"==".csv" if /I not "%EXT%"==".xlsx" if /I not "%EXT%"==".xls" goto UNSUPPORTED

copy /Y "%~1" "%EXPECTED_INPUT%" >nul
if errorlevel 1 goto COPYFAIL

echo Copied input to: "%EXPECTED_INPUT%"
goto CHECKINPUT

:NOFILE
echo No file provided. Expecting "%EXPECTED_INPUT%".
goto CHECKINPUT

:UNSUPPORTED
echo ERROR: Unsupported file type: %EXT%
echo Please provide a .csv, .xlsx, or .xls export.
pause
exit /b 1

:COPYFAIL
echo ERROR: Failed to copy input file into "%EXPECTED_INPUT%"
pause
exit /b 1

:CHECKINPUT
REM Safety check
if not exist "%EXPECTED_INPUT%" goto MISSINGINPUT

echo.
echo Running order processor...

REM Use RELATIVE venv path (avoids parentheses path parsing issues)
set "PYTHON_EXE=.venv\Scripts\python.exe"

if exist "%PYTHON_EXE%" goto RUNVENV
goto FIRSTTIMESETUP

:MISSINGINPUT
echo ERROR: Expected input not found: %EXPECTED_INPUT%
pause
exit /b 1

:RUNVENV
"%PYTHON_EXE%" "%SCRIPT%" "%CONFIG%"
if errorlevel 1 goto FAIL
goto DONE

:FIRSTTIMESETUP
echo.
echo ==========================================
echo First-time setup: creating .venv and installing packages
echo ==========================================
echo.

py --version >nul 2>&1
if errorlevel 1 goto NOPY

py -m venv .venv
if errorlevel 1 goto VENVFAIL

"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 goto PIPFAIL

"%PYTHON_EXE%" "%SCRIPT%" "%CONFIG%"
if errorlevel 1 goto FAIL
goto DONE

:NOPY
echo ERROR: Python launcher (py) not found.
echo Please install Python from python.org, then try again.
pause
exit /b 1

:VENVFAIL
echo ERROR: Failed to create virtual environment (.venv).
pause
exit /b 1

:PIPFAIL
echo ERROR: Failed to install required packages from requirements.txt
pause
exit /b 1

:FAIL
echo.
echo ERROR: Python script failed. See messages above.
pause
exit /b 1

:DONE
echo.
echo Done. Press any key to close.
pause >nul
exit /b 0