@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Infinity Wallpaper Studio
cd /d "%~dp0"

echo Starting Infinity Wallpaper Studio ...
echo.

set "PYEXE="

REM Resolve the user's home from this script's location as a fallback hint.
for %%A in ("%~dp0..\..\..") do set "HOMEHINT=%%~fA"

REM [1] Per-user Python installs, versions 3.9 to 3.14
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
  if not defined PYEXE if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
)
if not defined PYEXE for /d %%D in ("%HOMEHINT%\AppData\Local\Programs\Python\Python3*") do (
  if not defined PYEXE if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
)

REM [2] WorkBuddy bundled portable Python, any version folder
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
  if not defined PYEXE if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
)
if not defined PYEXE for /d %%D in ("%HOMEHINT%\.workbuddy\binaries\python\versions\*") do (
  if not defined PYEXE if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
)

REM [3] Other common install locations
if not defined PYEXE call :probe "C:\Python313\python.exe"
if not defined PYEXE call :probe "C:\Python312\python.exe"
if not defined PYEXE call :probe "C:\Python311\python.exe"
if not defined PYEXE call :probe "C:\Python310\python.exe"
if not defined PYEXE call :probe "C:\Python39\python.exe"
if not defined PYEXE call :probe "%USERPROFILE%\anaconda3\python.exe"
if not defined PYEXE call :probe "%USERPROFILE%\miniconda3\python.exe"
if not defined PYEXE call :probe "C:\ProgramData\anaconda3\python.exe"

REM [4] PATH lookup - skip the Microsoft Store stub, which always fails
if not defined PYEXE for /f "delims=" %%I in ('where python.exe 2^>nul') do call :probe_path "%%I"

REM [5] Python launcher as a last resort
if not defined PYEXE (
  where py.exe >nul 2>nul && set "PYEXE=py.exe"
)

goto :run

:probe
if defined PYEXE goto :eof
if exist "%~1" set "PYEXE=%~1"
goto :eof

:probe_path
if defined PYEXE goto :eof
echo %~1 | find /i "WindowsApps" >nul && goto :eof
if exist "%~1" set "PYEXE=%~1"
goto :eof

:run
if not defined PYEXE (
  echo [ERROR] No usable Python 3 interpreter found.
  echo.
  echo This PC only has the Microsoft Store python.exe shortcut on PATH.
  echo Install Python 3 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH", or edit this file and set PYEXE
  echo to the full path of a real python.exe.
  echo.
  echo Press any key to close.
  pause >nul
  endlocal
  exit /b 1
)

echo Using Python: %PYEXE%
echo Working dir : %CD%
echo.
"%PYEXE%" app.py %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo Server stopped normally.
) else (
  echo [ERROR] app.py exited with code %RC%. See the message above.
  echo Tip: keep app.py, index.html and the static folder together.
)
echo Press any key to close.
pause >nul
endlocal
exit /b %RC%
