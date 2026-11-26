"""Standalone entry point used to bundle Ruff beside the Windows Studio Core."""
from runpy import run_module


if __name__ == "__main__":
    run_module("ruff", run_name="__main__", alter_sys=True)
