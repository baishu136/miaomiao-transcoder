@echo off
rem Sunwoo 音频转换工具 - 双击启动
cd /d "%~dp0"
chcp 65001 >nul
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw qt_main.py
) else (
    python qt_main.py
)
