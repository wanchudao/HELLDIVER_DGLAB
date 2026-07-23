@echo off
chcp 65001 >nul
title HELLDIVER_DGLAB 一键启动

echo ================================================
echo   HELLDIVER_DGLAB V0.2 一键启动
echo ================================================
echo.
echo 即将启动:
echo   1. DG-LAB SOCKET v2 后端 (端口 9999)
echo   2. runner_real 实时检测 + 设备触发
echo.
echo 请确保:
echo   - 手机和电脑在同一局域网
echo   - DG-LAB APP 已安装
echo   - 游戏设置为无边框窗口模式
echo ================================================
echo.

set "PROJECT_DIR=%~dp0"
set "BACKEND_DIR=%PROJECT_DIR%official_v2\socket\v2\backend"

echo [1/2] 启动后端...

start "DG-LAB Backend" cmd /c "cd /d "%BACKEND_DIR%" && npm start"

echo        等待后端启动 (5秒)...
timeout /t 5 /nobreak >nul

echo [2/2] 启动实时检测...
echo.
echo ================================================
echo   启动后请:
echo     1. 用 DG-LAB APP 扫描弹出的 QR 码
echo     2. 进入游戏任务
echo     3. 按 P 键开始检测
echo   关闭: 按 Ctrl+C 退出
echo ================================================
echo.

start "HELLDIVER Runner" cmd /c "cd /d "%PROJECT_DIR%" && python src/runner_real.py --enable-trigger"

echo.
echo 启动完成。两个窗口已打开。
echo 关闭本窗口不影响运行。
echo.
pause
