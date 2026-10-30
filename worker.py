"""JSON-lines bridge between Spring Boot and the original coding-agent tools."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import signal
from pathlib import Path
import subprocess
import sys


def emit(kind: str, content: str = "", **extra) -> None:
    print(json.dumps({"type": kind, "content": content, **extra}, ensure_ascii=False), flush=True)


def shell_command(workspace: Path, command: str) -> str:
    """Commands are an explicit local-owner opt-in, not a filesystem sandbox."""
    if os.getenv("AGENT_ALLOW_SHELL", "false").lower() != "true":
        return "Error: Shell execution is disabled. Use the file tools instead."
    if len(command) > 12000:
        return "Error: Command is too long."
    try:
        process = subprocess.Popen(command, shell=True, cwd=workspace,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, errors="replace",
                                   start_new_session=os.name != "nt")
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=5)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.kill()
            process.communicate(timeout=5)
            return "Error: Command exceeded the 30-second timeout."
        return f"Exit code: {process.returncode}\n{(stdout + stderr)[-30000:]}"
    except subprocess.TimeoutExpired:
        return "Error: Command exceeded the 30-second timeout."


def load_core(workspace: Path, model: str):
    os.environ["MODEL_ID"] = model
    os.chdir(workspace)
    spec = importlib.util.spec_from_file_location("minty_core", Path(__file__).with_name("agent.py"))
    core = importlib.util.module_from_spec(spec)
    # Original CLI diagnostics must never corrupt the JSON-lines channel.
    with contextlib.redirect_stdout(sys.stderr):
        spec.loader.exec_module(core)
    core.client = core.Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
                                 timeout=90.0, max_retries=1)
    return core


def execute(request: dict, core, workspace: Path) -> None:
    prompt = str(request.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("任务内容不能为空")
    allowed = {"read_file", "write_file", "edit_file", "TodoWrite", "task_create",
               "task_get", "task_update", "task_list", "load_skill", "compress"}
    if os.getenv("AGENT_ALLOW_SHELL", "false").lower() == "true":
        allowed.add("bash")
    tools = [tool for tool in core.TOOLS if tool["name"] in allowed]
    handlers = {name: handler for name, handler in core.TOOL_HANDLERS.items() if name in allowed}
    handlers["bash"] = lambda command: shell_command(workspace, command)
    messages = [{"role": item["role"], "content": str(item["content"])}
                for item in request.get("history", [])[-30:]
                if item.get("role") in ("user", "assistant") and item.get("content")]
    messages.append({"role": "user", "content": prompt})
    max_steps = max(1, min(int(request.get("maxSteps", 24)), 40))
    model = request.get("model") or os.getenv("MODEL_ID", "claude-sonnet-4-6")
    system = ("You are Minty, a helpful personal coding assistant. Reply in the user's language. "
              "Use tools to actually complete requested work; never claim a tool ran when it did not. "
              "All paths must be relative to the session workspace. Work only inside it. "
              "Use task_create/task_update for multi-step requests. Do not read secrets or environment "
              "files. Do not start servers, long-running processes, or background commands. "
              "Shell commands run via cmd.exe on Windows and /bin/sh elsewhere. "
              "If a capability is unavailable, explain it honestly. "
              f"Available skills: {core.SKILLS.descriptions()}")
    emit("status", "正在理解任务")
    for _ in range(max_steps):
        core.microcompact(messages)
        if core.estimate_tokens(messages) > 60000:
            messages = compact(core, messages, model)
        with core.client.messages.stream(model=model, system=system, messages=messages,
                                         tools=tools, max_tokens=4096) as stream:
            for chunk in stream.text_stream:
                emit("text", chunk)
            response = stream.get_final_message()
        emit("usage", inputTokens=response.usage.input_tokens,
             outputTokens=response.usage.output_tokens)
        blocks = [b.model_dump(exclude_none=True) for b in response.content]
        messages.append({"role": "assistant", "content": blocks})
        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            if response.stop_reason == "max_tokens":
                raise RuntimeError("回答达到单轮长度上限，请缩小任务后重试。")
            emit("done", "任务完成")
            return
        results = []
        compress = False
        for call in calls:
            emit("tool_start", json.dumps({"name": call.name, "input": call.input}, ensure_ascii=False))
            try:
                if call.name not in allowed:
                    raise ValueError("This tool is unavailable in the web application")
                # Block credential files even when a model requests them.
                if call.name in {"read_file", "write_file", "edit_file"}:
                    path = core.safe_path(call.input["path"])
                    if any(part.startswith(".") for part in path.relative_to(workspace).parts):
                        raise ValueError("Hidden/configuration files are not accessible through web tools")
                with contextlib.redirect_stdout(sys.stderr):
                    output = str(handlers[call.name](**call.input))[:40000]
                compress = compress or call.name == "compress"
            except Exception as exc:
                output = f"Error: {exc}"
            emit("tool_result", json.dumps({"name": call.name, "output": output}, ensure_ascii=False))
            results.append({"type": "tool_result", "tool_use_id": call.id,
                            "content": output, "is_error": output.startswith("Error:")})
        messages.append({"role": "user", "content": results})
        if compress:
            messages = compact(core, messages, model)
    raise RuntimeError(f"已达到 {max_steps} 轮工具调用上限；可以继续发送消息处理剩余任务。")


def compact(core, messages: list, model: str) -> list:
    emit("status", "正在压缩对话上下文")
    response = core.client.messages.create(model=model, max_tokens=2000, messages=[
        {"role": "user", "content": "Summarize task progress and file paths for continuity:\n"
         + json.dumps(messages, ensure_ascii=False, default=str)[-80000:]}])
    emit("usage", inputTokens=response.usage.input_tokens, outputTokens=response.usage.output_tokens)
    summary = "\n".join(b.text for b in response.content if b.type == "text")
    return [{"role": "user", "content": "[Conversation summary]\n" + summary}]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stdin.reconfigure(encoding="utf-8")
        request = json.loads(sys.stdin.readline())
        workspace = Path(args.workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key or key in {"your-api-key", "sk-ant-xxx"}:
            raise ValueError("请先在项目根目录 .env 中配置有效的 ANTHROPIC_API_KEY")
        core = load_core(workspace, request.get("model") or os.getenv("MODEL_ID", "claude-sonnet-4-6"))
        execute(request, core, workspace)
        return 0
    except Exception as exc:
        # No traceback or request headers are exposed to the UI.
        message = str(exc)
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if key:
            message = message.replace(key, "[redacted]")
        emit("error", message[:1500])
        return 1


if __name__ == "__main__":
    sys.exit(main())
