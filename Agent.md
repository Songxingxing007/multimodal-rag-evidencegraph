# Agent.md

1. 前端代码统一放在 `frontend/`，后端代码统一放在 `backend/`，不得混放。
2. 后端外部配置（API Key、Token、数据库连接等）统一由 `backend/.env` 管理，不得硬编码或提交到 Git；根目录 `.gitignore` 必须忽略 `.env` 和 `backend/.env`，可保留无密钥的 `.env.example`。
3. 后端服务必须使用 `uv` 创建独立虚拟环境 `backend/.venv`；启动、安装依赖和运行测试均使用该环境，不得使用全局 Python。
