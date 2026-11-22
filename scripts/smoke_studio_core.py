"""Smoke-test the packaged Studio Core JSONL handshake on the build host."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


def main() -> int:
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"Core executable not found: {executable}")

    with tempfile.TemporaryDirectory(prefix="kardecagent-studio-smoke-") as project:
        process = subprocess.Popen(
            [str(executable), "studio", "--project", project],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        messages: queue.Queue[str | None] = queue.Queue()

        def read_first_line() -> None:
            assert process.stdout is not None
            messages.put(process.stdout.readline())

        reader = threading.Thread(target=read_first_line, daemon=True)
        reader.start()
        try:
            try:
                line = messages.get(timeout=45)
            except queue.Empty:
                stderr = ""
                if process.poll() is not None and process.stderr is not None:
                    stderr = process.stderr.read()
                raise SystemExit(f"Core did not emit runtime.ready within 45 seconds. {stderr}")
            if not line:
                stderr = process.stderr.read() if process.stderr is not None else ""
                raise SystemExit(f"Core exited before the handshake. {stderr}")
            message = json.loads(line)
            if message.get("type") != "runtime.ready" or message.get("schema_version") != 1:
                raise SystemExit(f"Unexpected Core handshake: {message!r}")
            print("Packaged Core handshake OK:", json.dumps(message, ensure_ascii=False))
        finally:
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
