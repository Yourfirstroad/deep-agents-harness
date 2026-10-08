@echo off
REM ---------------------------------------------------------------------------
REM Windows 启动器:run_watchdog.py
REM   scripts\run_watchdog.bat           前台运行
REM   scripts\run_watchdog.bat --detach  后台运行
REM   scripts\run_watchdog.bat --stop    停止后台实例
REM   scripts\run_watchdog.bat --status  查看后台状态
REM 双击本 .bat 文件 = 前台运行,关闭窗口即停止。
REM ---------------------------------------------------------------------------
setlocal
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.."

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python "%SCRIPT_DIR%run_watchdog.py" %*
) else (
    where py >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        py "%SCRIPT_DIR%run_watchdog.py" %*
    ) else (
        echo [error] 未找到 python / py,请先安装 Python 并加入 PATH。
        popd
        exit /b 1
    )
)

set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
