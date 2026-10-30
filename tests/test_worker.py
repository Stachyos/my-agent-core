"""Bridge contract tests; no real model calls or credentials required."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import worker


class Block(NS):
    def model_dump(self, **kwargs):
        return vars(self)


class Stream:
    def __init__(self, response, text=()):
        self.response = response
        self.text_stream = iter(text)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get_final_message(self):
        return self.response


def response(blocks, reason="end_turn"):
    return NS(content=blocks, stop_reason=reason, usage=NS(input_tokens=9, output_tokens=4))


class WorkerTest(unittest.TestCase):
    def core(self, workspace, streams, writer):
        def safe_path(value):
            resolved = (workspace / value).resolve()
            if not resolved.is_relative_to(workspace):
                raise ValueError("Path escapes workspace")
            return resolved
        return NS(TOOLS=[{"name": "write_file"}], TOOL_HANDLERS={"write_file": writer},
                  SKILLS=NS(descriptions=lambda: "none"), safe_path=safe_path,
                  microcompact=lambda messages: None, estimate_tokens=lambda messages: 1,
                  client=NS(messages=NS(stream=lambda **kw: next(streams))))

    def test_stream_and_tool_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            call = Block(type="tool_use", id="tool1", name="write_file",
                         input={"path": "hello.txt", "content": "你好"})
            streams = iter([Stream(response([call], "tool_use")),
                            Stream(response([Block(type="text", text="完成")]), ["完", "成"])])
            def write(path, content):
                (workspace / path).write_text(content, encoding="utf-8")
                return "Wrote file"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                worker.execute({"prompt": "写一个文件"}, self.core(workspace, streams, write), workspace)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual("你好", (workspace / "hello.txt").read_text(encoding="utf-8"))
            self.assertEqual("完成", "".join(e["content"] for e in events if e["type"] == "text"))
            self.assertEqual("done", events[-1]["type"])
            self.assertEqual(2, sum(e["type"] == "usage" for e in events))
            self.assertEqual("write_file", json.loads(next(e["content"] for e in events if e["type"] == "tool_start"))["name"])

    def test_path_escape_and_hidden_files_never_reach_tool(self):
        for path in ("../escape.txt", ".env", "nested/.secret"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as folder:
                workspace = Path(folder).resolve()
                call = Block(type="tool_use", id="tool1", name="write_file", input={"path": path, "content": "x"})
                streams = iter([Stream(response([call], "tool_use")), Stream(response([]))])
                def forbidden(**kwargs):
                    self.fail("Unsafe path reached tool")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    worker.execute({"prompt": "test"}, self.core(workspace, streams, forbidden), workspace)
                events = [json.loads(line) for line in output.getvalue().splitlines()]
                result = next(e for e in events if e["type"] == "tool_result")
                self.assertTrue(json.loads(result["content"])["output"].startswith("Error:"))

    def test_shell_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {"AGENT_ALLOW_SHELL": "false"}):
            self.assertIn("disabled", worker.shell_command(Path.cwd(), "echo hello"))

    def test_step_limit_is_a_failure_not_fake_success(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            call = Block(type="tool_use", id="tool1", name="write_file", input={"path": "x", "content": "x"})
            streams = iter([Stream(response([call], "tool_use"))])
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                worker.execute({"prompt": "test", "maxSteps": 1}, self.core(workspace, streams, lambda **kw: "ok"), workspace)


if __name__ == "__main__":
    unittest.main()
