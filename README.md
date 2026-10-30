# my-agent-core

个人编程 Agent 的初始代码底座，包含工具调用、文件操作、任务管理、子代理、后台任务、团队协作与上下文压缩。

## 运行

需要 Python 3.10 或以上版本。在项目目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env，填写自己的 API Key 和模型名称
.venv\Scripts\python agent.py
```

命令：`/compact` 压缩上下文、`/tasks` 查看任务、`/team` 查看团队、`/inbox` 查看消息；输入 `exit` 退出。

Agent 可以执行本机命令及修改文件；建议在独立工作目录运行。模型调用需要有效的 API 配置。

## 来源与许可

运行代码基于 [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 的 `agents/s_full.py` 整理，保留 MIT 许可证及 shareAI Lab 版权声明。当前尚未添加个人业务功能。
