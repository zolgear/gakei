@echo off
setlocal enabledelayedexpansion

rem Start GAKEI locally on Windows (ADR-0012).
rem Usage: run.bat [--host H] [--port P] [--data-dir DIR] [--no-browser]
rem
rem Keep this file ASCII only. cmd reads batch files in the console code page,
rem and multibyte comments or messages get misparsed as commands. Japanese
rem messages are printed by the Python launcher, which writes UTF-8.
rem Comments in run.bat and run.sh are kept in English for the same reason, so
rem the two launchers can be maintained side by side.
rem
rem On failure the window waits for a key press, so that the error stays
rem readable when the file is started by double-click. Set GAKEI_NO_PAUSE=1
rem to skip the wait (CI does).

set "REPO_ROOT=%~dp0"

rem Add the default location of the uv installer to PATH if it exists.
set "UV_DEFAULT_DIR=%USERPROFILE%\.local\bin"
echo ";%PATH%;" | find /I ";%UV_DEFAULT_DIR%;" >nul
if errorlevel 1 if exist "%UV_DEFAULT_DIR%" set "PATH=%PATH%;%UV_DEFAULT_DIR%"

where uv >nul 2>nul
if errorlevel 1 (
    rem Ask only when the script runs in an interactive console.
    set "STDIN_REDIRECTED=True"
    for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "[Console]::IsInputRedirected"`) do set "STDIN_REDIRECTED=%%i"

    if /I "!STDIN_REDIRECTED!"=="True" (
        echo uv was not found. Install it from https://docs.astral.sh/uv/ and run this script again. 1>&2
        goto :fail
    )

    set "ANSWER="
    set /p "ANSWER=uv was not found. Install it with the official installer? [Y/n] "
    if not defined ANSWER set "ANSWER=Y"
    if /I not "!ANSWER:~0,1!"=="Y" (
        echo Canceled. Install uv manually: https://docs.astral.sh/uv/ 1>&2
        goto :fail
    )

    powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    if errorlevel 1 (
        echo Failed to install uv. See https://docs.astral.sh/uv/ 1>&2
        goto :fail
    )

    set "PATH=%USERPROFILE%\.local\bin;%PATH%"

    where uv >nul 2>nul
    if errorlevel 1 (
        echo uv was installed but is not on PATH. Open a new window and run this script again. 1>&2
        goto :fail
    )
)

cd /d "%REPO_ROOT%backend"
if errorlevel 1 (
    echo Could not change to the backend directory: %REPO_ROOT%backend 1>&2
    goto :fail
)

uv run --frozen python -m app.launch %*
if errorlevel 1 goto :fail
exit /b 0

:fail
set "EXIT_CODE=%errorlevel%"
if "%EXIT_CODE%"=="0" set "EXIT_CODE=1"
if not defined GAKEI_NO_PAUSE (
    echo.
    pause
)
exit /b %EXIT_CODE%
