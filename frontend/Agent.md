# Agent.md

## 工作目录

```powershell
cd frontend
```

## 安装依赖

```powershell
pnpm install --frozen-lockfile
```

## 启动开发服务

```powershell
pnpm dev
```

默认访问地址：`http://127.0.0.1:8443/`

如需显式指定地址：

```powershell
pnpm dev -- --host 127.0.0.1 --port 8443
```

## 构建与预览

```powershell
pnpm build
pnpm preview
```

## 前后端接口测试

请先启动 `backend` 服务，再启动前端开发服务：

```powershell
pnpm test:integration
```

所有前端命令必须在 `frontend/` 目录下执行。
