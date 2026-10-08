@echo off
rem DevMind-AI 前端一键启动
cd /d %~dp0

if not exist node_modules (
    echo [1/3] 安装前端依赖...
    call npm install
)

echo [2/3] 启动前端开发服务器...
start "" http://localhost:5173

echo [3/3] Vite 运行中：http://localhost:5173
call npm run dev

pause
