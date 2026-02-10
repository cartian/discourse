from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import click


@dataclass
class InvokeResult:
    text: str
    session_id: str
    raw: list = field(default_factory=list)
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    duration_ms: int | None = None
    duration_api_ms: int | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    is_error: bool = False
    wall_clock_ms: float | None = None


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

    t0 = time.perf_counter()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    wall_clock_ms = (time.perf_counter() - t0) * 1000

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

    # Extract metadata from CLI JSON events
    model = None
    input_tokens = None
    output_tokens = None
    cache_read_tokens = None
    cache_creation_tokens = None
    duration_ms = None
    duration_api_ms = None
    cost_usd = None
    num_turns = None
    is_error = False

    for event in events:
        if not isinstance(event, dict):
            continue
        etype = event.get("type")

        if etype == "system":
            if not model:
                model = event.get("model")

        elif etype == "assistant":
            msg = event.get("message", {})
            if not model:
                model = msg.get("model")
            usage = msg.get("usage", {})
            if usage:
                input_tokens = usage.get("input_tokens", input_tokens)
                output_tokens = usage.get("output_tokens", output_tokens)
                cache_read_tokens = usage.get("cache_read_input_tokens", cache_read_tokens)
                cache_creation_tokens = usage.get("cache_creation_input_tokens", cache_creation_tokens)

        elif etype == "result":
            duration_ms = event.get("duration_ms", duration_ms)
            duration_api_ms = event.get("duration_api_ms", duration_api_ms)
            cost_usd = event.get("total_cost_usd", cost_usd)
            num_turns = event.get("num_turns", num_turns)
            is_error = event.get("is_error", False)
            # result event may also carry usage
            usage = event.get("usage", {})
            if usage:
                input_tokens = usage.get("input_tokens", input_tokens)
                output_tokens = usage.get("output_tokens", output_tokens)
                cache_read_tokens = usage.get("cache_read_input_tokens", cache_read_tokens)
                cache_creation_tokens = usage.get("cache_creation_input_tokens", cache_creation_tokens)

    return InvokeResult(
        text=text,
        session_id=actual_session_id,
        raw=events,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        duration_ms=duration_ms,
        duration_api_ms=duration_api_ms,
        cost_usd=cost_usd,
        num_turns=num_turns,
        is_error=is_error,
        wall_clock_ms=wall_clock_ms,
    )


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
