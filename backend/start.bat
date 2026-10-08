@echo off
rem DevMind-AI 后端一键启动
cd /d %~dp0

echo [1/2] 检查并安装后端依赖...
python -m pip install -r requirements.txt

echo [2/2] 启动后端服务：http://127.0.0.1:15200
python -m uvicorn app.main:app --host 0.0.0.0 --port 15200 --reload

pause
