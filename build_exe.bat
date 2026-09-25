@echo off
chcp 65001 >nul
rem 用 PyInstaller 打包喵喵转码器（文件夹模式）
rem 依赖：Python、PyInstaller、PyQt6，以及本地 ffmpeg/ffprobe
cd /d "%~dp0"
python -m PyInstaller "%~dp0build\MiaomiaoTranscoder.spec" ^
  --workpath "%~dp0build\work" ^
  --distpath "%~dp0dist" ^
  --clean --noconfirm

if errorlevel 1 (
  echo [错误] 打包失败
  pause
  exit /b 1
)

rem 复制 ffmpeg 到输出目录
if exist "%~dp0dist\MiaomiaoTranscoder\ffmpeg" rd /s /q "%~dp0dist\MiaomiaoTranscoder\ffmpeg"
if exist "%~dp0ffmpeg\ffmpeg.exe" (
  xcopy /e /i /q "%~dp0ffmpeg" "%~dp0dist\MiaomiaoTranscoder\ffmpeg" >nul
) else (
  echo [提示] 未找到 ffmpeg\ffmpeg.exe；请将 ffmpeg.exe 和 ffprobe.exe 放入 ffmpeg 目录。
)

echo.
echo [完成] 打包输出：%~dp0dist\MiaomiaoTranscoder\
echo        运行喵喵转码器.exe即可启动
pause
