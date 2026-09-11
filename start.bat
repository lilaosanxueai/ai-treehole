@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   树洞挚友 - AI 对话树洞
echo   启动后自动打开 http://127.0.0.1:8311
echo   关闭窗口或按 Ctrl+C 退出
echo ============================================
python app.py
pause
