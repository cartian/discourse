from __future__ import annotations

import json
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import click


@dataclass
class InvokeResult:
    text: str
    session_id: str
    raw: dict


def invoke_claude(
    prompt: str,
    session_id: str | None = None,
    system_prompt: str | None = None,
    timeout: int = 300,
) -> InvokeResult:
    """Invoke the claude CLI and return parsed output."""
    cmd = ["claude", "-p", "--output-format", "json"]

    if session_id:
        cmd.extend(["--resume", session_id])
    else:
        session_id = str(uuid.uuid4())
        cmd.extend(["--session-id", session_id])

    if system_prompt and "--resume" not in cmd:
        cmd.extend(["--system-prompt", system_prompt])

    cmd.extend(["--permission-mode", "bypassPermissions"])
    cmd.append(prompt)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited with code {result.returncode}\n"
            f"stderr: {result.stderr}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        debug_dir = Path(".discourse-debug")
        debug_dir.mkdir(exist_ok=True)
        dump_path = debug_dir / f"raw-{session_id}.txt"
        dump_path.write_text(result.stdout)
        raise RuntimeError(
            f"Invalid JSON from claude CLI (raw output saved to {dump_path}): {e}"
        )

    # claude --output-format json returns a JSON array of event objects:
    #   {"type": "system", "subtype": "init", "session_id": ...}
    #   {"type": "assistant", "message": {"content": [...]}, "session_id": ...}
    #   {"type": "result", "subtype": "success", "result": "...", "session_id": ...}
    events = data if isinstance(data, list) else [data]

    text = ""
    actual_session_id = session_id
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            text = event.get("result", "")
            actual_session_id = event.get("session_id", actual_session_id)
        elif event.get("type") == "system" and not actual_session_id:
            actual_session_id = event.get("session_id", actual_session_id)

    if not text:
        # Fallback: look for assistant message content blocks
        for event in events:
            if isinstance(event, dict) and event.get("type") == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block["text"]
                        break
                if text:
                    break

    return InvokeResult(text=text, session_id=actual_session_id, raw=events)


def handle_error(turn_number: int, participant_name: str, error: Exception) -> str | None:
    """Prompt user to retry, skip, or abort on error. Returns 'retry', 'skip', or raises SystemExit."""
    click.echo(f"\n{'='*60}")
    click.echo(f"ERROR during Turn {turn_number} ({participant_name}):")
    click.echo(f"  {error}")
    click.echo(f"{'='*60}")

    while True:
        choice = click.prompt(
            "[r]etry / [s]kip this turn / [a]bort",
            type=click.Choice(["r", "s", "a"], case_sensitive=False),
        )
        if choice == "r":
            return "retry"
        elif choice == "s":
            return "skip"
        elif choice == "a":
            raise SystemExit("Aborted by user")


def check_referee_request(text: str) -> tuple[str, str | None]:
    """Check for <!-- REFEREE: question --> markers. Returns (cleaned_text, question_or_none)."""
    match = re.search(r"<!--\s*REFEREE:\s*(.*?)\s*-->", text, re.DOTALL)
    if match:
        question = match.group(1).strip()
        cleaned = text[:match.start()] + text[match.end():]
        return cleaned.strip(), question
    return text, None
