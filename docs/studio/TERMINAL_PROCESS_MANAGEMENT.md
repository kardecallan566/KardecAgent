# Integrated terminal and process lifecycle

The Studio currently manages one integrated shell process per window. The selected project directory is used as its working directory. On Windows the supported shells are PowerShell and CMD; development builds on Unix use Bash.

## Process supervision

- The native Tauri layer tracks the shell PID and exit code.
- The UI queries terminal diagnostics after startup, natural exit, and explicit shutdown.
- Shutdown closes the shell's stdin first so it can exit normally; after a one-second grace period the process is terminated if it remains alive.
- A new terminal cannot be started while the previous shell is still running.
- Output is read from stdout and stderr concurrently. Each line is limited to 16 KiB; oversized lines are discarded and reported without stopping the reader.
- The UI retains at most 1,000 output lines and offers an explicit clear action.

## Current scope and limitations

This is a pipe-backed command shell, not a full pseudo-terminal (PTY). It supports sequential command input and streaming output, but interactive terminal applications that require terminal control sequences, cursor movement, raw mode, or password prompts may not behave like they do in Windows Terminal. Multi-tab terminal sessions, process trees, task-level cancellation, and PTY support remain future work.

The integrated shell is distinct from the agent's restricted command tool. The agent's command tool remains argv-based and allowlisted; the user's integrated terminal is a general-purpose shell started explicitly from the IDE.
