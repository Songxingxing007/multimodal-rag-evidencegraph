# Agent.md

## 工作目录

```powershell
cd backend
```

## 初始化独立环境

```powershell
uv sync --group dev
```

所有后端命令必须使用 `backend/.venv`，禁止使用全局 Python。

## 启动后端服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

或使用项目脚本：

```powershell
.\scripts\start_server.ps1
```

服务地址：`http://127.0.0.1:8010/`

API 文档：`http://127.0.0.1:8010/docs`

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\smoke_test.py --base-url http://127.0.0.1:8010
```
