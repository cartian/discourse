from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .claude import InvokeResult


class AuditLog:
    """Append-only JSONL audit log for a discourse session."""

    def __init__(self, session_dir: Path):
        self.path = session_dir / "audit.jsonl"
        self._file = open(self.path, "a")

    def _write(self, event: dict) -> None:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        try:
            self._file.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._file.flush()
        except OSError as e:
            print(f"[audit] write error: {e}", file=sys.stderr)

    def close(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass

    def log_session_start(
        self,
        mode: str,
        topic: str,
        participants: dict[str, dict[str, str]],
        config: dict | None = None,
    ) -> None:
        event: dict = {
            "type": "session_start",
            "mode": mode,
            "topic": topic,
            "participants": participants,
        }
        if config:
            event["config"] = config
        self._write(event)

    def log_turn_start(
        self,
        turn: int,
        participant_key: str,
        participant_name: str,
    ) -> None:
        self._write({
            "type": "turn_start",
            "turn": turn,
            "participant": participant_key,
            "participant_name": participant_name,
        })

    def log_invoke(
        self,
        turn: int,
        participant_key: str,
        result: InvokeResult,
        prompt: str,
        system_prompt: str | None = None,
        is_new_session: bool = False,
    ) -> None:
        event: dict = {
            "type": "invoke",
            "turn": turn,
            "participant": participant_key,
            "session_id": result.session_id,
            "is_new_session": is_new_session,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cache_read_tokens": result.cache_read_tokens,
            "cache_creation_tokens": result.cache_creation_tokens,
            "duration_ms": result.duration_ms,
            "duration_api_ms": result.duration_api_ms,
            "wall_clock_ms": round(result.wall_clock_ms, 1) if result.wall_clock_ms else None,
            "cost_usd": result.cost_usd,
            "num_turns": result.num_turns,
            "is_error": result.is_error,
            "response_length": len(result.text),
            "prompt": prompt,
        }
        if system_prompt is not None:
            event["system_prompt"] = system_prompt
        self._write(event)

    def log_error(
        self,
        turn: int,
        participant_key: str,
        participant_name: str,
        error: Exception,
        user_action: str,
    ) -> None:
        self._write({
            "type": "error",
            "turn": turn,
            "participant": participant_key,
            "participant_name": participant_name,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "user_action": user_action,
        })

    def log_referee(
        self,
        turn: int,
        question: str,
        answer: str,
    ) -> None:
        self._write({
            "type": "referee",
            "turn": turn,
            "question": question,
            "answer": answer,
        })

    def log_check_in(
        self,
        turn: int,
        choice: str,
        message: str | None = None,
    ) -> None:
        event: dict = {
            "type": "check_in",
            "turn": turn,
            "choice": choice,
        }
        if message is not None:
            event["message"] = message
        self._write(event)

    def log_session_end(
        self,
        status: str,
        total_turns: int,
    ) -> None:
        self._write({
            "type": "session_end",
            "status": status,
            "total_turns": total_turns,
        })
