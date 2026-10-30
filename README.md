# Minty Code · 薄荷编程助手

一个供自己使用的网页版编程助手。奶油白与薄荷绿界面，聊天、任务、工作文件和运行记录集中在同一个工作台。

## 已实现

- 多会话聊天、逐步显示回答、刷新恢复最近运行。
- 实际调用 Anthropic 兼容 Messages API，展示工具调用过程。
- 每个会话独立工作目录；上传 UTF-8 代码文件、预览与下载生成文件。
- 聊天、运行、事件和 Token 统计持久化；任务列表读取实际任务文件。
- 停止任务、运行超时和调用轮数上限；服务重启后标记中断任务。
- ECharts 展示最近七天的真实运行次数与 Token 用量，无演示数据。
- 命令执行默认关闭；网页不接收或保存 API Key。

第一版聚焦单人使用，没有多人账号或跨运行后台队友。原终端版保留在 `agent.py`。

## 技术栈

| 层 | 实现 |
| --- | --- |
| 前端 | Vue 3、Element Plus、ECharts、Vite、Axios |
| 后端 | Spring Boot 3.4.13、Spring Data JPA、Maven、Java 17 |
| 数据库 | PostgreSQL 14.10（Compose 固定版本） |
| Agent | Python 3.10+，复用现有文件与任务工具 |

浏览器 → Spring Boot API → 独立 Python Worker → 模型与工作文件。Java 负责数据库、会话和执行生命周期；Python 负责模型与工具循环。

## 一键启动（Docker Desktop）

先启动 Docker Desktop 的 Linux 容器引擎，然后在本项目目录打开 PowerShell：

```powershell
Copy-Item .env.example .env
# 编辑 .env，填写 ANTHROPIC_API_KEY、MODEL_ID。
# 如已存在 .env，直接编辑它，不要覆盖。
.\start.ps1
```

打开 <http://127.0.0.1:5173>。首次需要下载镜像和构建依赖。未配置有效密钥时仍可打开工作台和上传文件，发送任务会明确提示配置缺失。

修改 `.env` 后重新运行 `.\start.ps1` 更新服务。停止应用：

```powershell
.\start.ps1 -Stop
```

停止会保留容器、数据库和工作文件，可在 Docker Desktop 再次启动。Compose 只将网页端口绑定本机，不暴露数据库端口。

启动成功后，Docker Desktop 的 **Containers** 中会出现 `minty-code`，展开可见 `frontend`、`backend` 和 `db`。之后可以直接在 Docker Desktop 启动或停止这一组容器。前后端与数据库均通过健康检查后再提供服务。

Docker 中的数据库保存在 `minty-code_postgres-data` 卷，工作文件保存在 `minty-code_agent-data` 卷，可在 **Volumes** 中查看。它们不上传 GitHub。`docker compose down` 保留数据；不要加 `-v`，该参数会删除数据卷。更换已初始化数据库的密码需要先在 PostgreSQL 中修改，单改 `.env` 不会改变已有数据库密码。

从本地开发切换时先运行 `.\start-dev.ps1 -Stop` 释放网页端口。两个模式使用不同数据存储，不会自动互相同步；本机原数据的迁移备份放在 `data/backups/`。

## 本地开发（不使用 Docker）

需要 Java 17、Maven 3.6.3+、Node.js 22、Python 3.10+ 和运行中的 PostgreSQL 14.10。

当前电脑已经准备好了独立的 PostgreSQL 14.10 环境。后续可以在根目录运行 `.\start-dev.ps1 -SkipBuild` 启动，或 `.\start-dev.ps1 -Stop` 停止前后端。修改 Java 代码后运行 `.\start-dev.ps1` 重新构建。日志在 `data/`，不会提交 Git。此脚本也支持在 `.env` 配置 `JAVA_HOME`、`MAVEN_CMD`、`POSTGRES_BIN` 来指定本机工具路径。

1. 创建数据库和用户，例如 `minty`。在根 `.env` 中设置连接信息：

```dotenv
DATABASE_URL=jdbc:postgresql://127.0.0.1:5432/minty
DATABASE_USERNAME=minty
DATABASE_PASSWORD=你的本地数据库密码
ANTHROPIC_API_KEY=你的模型密钥
MODEL_ID=你的模型名称
# 可选 Anthropic 兼容网关
# ANTHROPIC_BASE_URL=https://api.anthropic.com
```

2. 在项目根目录创建 Python 环境并安装依赖：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

将 `.env` 的 `AGENT_PYTHON` 设置为此环境 `python.exe` 的绝对路径。不要加引号；Windows 路径可用 `/`。

3. 在两个终端分别启动后端和前端：

```powershell
cd backend
mvn spring-boot:run
```

```powershell
cd frontend
npm ci
npm run dev
```

访问 Vite 输出的本机地址。`/api` 代理到 `127.0.0.1:8080`。PyCharm 可配置前端运行；Java 后端可在 IntelliJ IDEA 中打开 `backend/pom.xml`。

## 使用

1. 点击新的灵感，在文件面板上传代码，或直接要求助手创建新文件。
2. 发送具体任务，例如「阅读这些文件，解释入口和主要逻辑，先不要修改」。
3. 查看工具与任务状态；需要中断时点击停止。
4. 预览或下载修改后的文件。会话工作目录与仓库源代码分开，上传不会自动修改原项目。

`AGENT_ALLOW_SHELL=true` 开启命令工具，修改后重启后端。命令使用服务器用户权限，工作目录限制不等于操作系统沙箱；请只在自己的电脑和可信代码上开启。Docker 模式下命令在容器内运行。

## 目录

```text
frontend/        Vue 网页与可视化
backend/         Spring Boot API、JPA、执行管理与测试
worker.py        JSON Lines 流式桥接
agent.py         原终端 Agent 底座
tests/           Python 工具与 SDK 协议测试
infra/           Nginx 与前端容器配置
compose.yaml     PostgreSQL 14.10 + 后端 + 前端
.env.example     配置模板，真实 .env 不提交
```

本地数据在 `data/`；Docker 数据在 `postgres-data` 和 `agent-data` 卷。均不进入 Git。

## 验证

```powershell
python -m unittest discover -s tests -v
cd backend
mvn test
cd ../frontend
npm run build
```

测试通过本地接口夹具验证流式协议，不消耗真实模型额度。真实模型仍需要有效密钥、模型名称与网络连接。

## 来源与许可

`agent.py` 基于 [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 的 `agents/s_full.py`，保留 MIT 许可证与 shareAI Lab 版权声明。网页界面、Spring Boot 服务、数据库持久化和 Worker 桥接在此基础上新增。许可见 [LICENSE](LICENSE)。
