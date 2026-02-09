from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml


class Document:
    """Manages the workshop document file with git versioning."""

    def __init__(self, output_dir: Path, topic: str, source_file: str | None = None):
        self.output_dir = output_dir
        self.file_path = output_dir / "document.md"

        self._init_git()

        if source_file:
            source = Path(source_file)
            if not source.exists():
                raise FileNotFoundError(f"Source file not found: {source_file}")
            self.file_path.write_text(source.read_text())
            self._git_commit("Initialize document from source file")
        else:
            self.file_path.write_text(f"# {topic}\n")
            self._git_commit("Initialize empty document")

    def read(self) -> str:
        return self.file_path.read_text()

    def write(self, content: str, turn_number: int) -> None:
        self.file_path.write_text(content)
        self._git_commit(f"Author revision — turn {turn_number}")

    def _init_git(self) -> None:
        git_dir = self.output_dir / ".git"
        if not git_dir.exists():
            subprocess.run(
                ["git", "init"],
                cwd=self.output_dir,
                capture_output=True,
                check=True,
            )
            # Configure local user for this repo so commits work without global config
            subprocess.run(
                ["git", "config", "user.name", "Discourse Workshop"],
                cwd=self.output_dir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "workshop@discourse.local"],
                cwd=self.output_dir,
                capture_output=True,
                check=True,
            )

    def _git_commit(self, message: str) -> None:
        subprocess.run(
            ["git", "add", "document.md"],
            cwd=self.output_dir,
            capture_output=True,
            check=True,
        )
        # Also stage the editorial log if it exists
        log_path = self.output_dir / "editorial-log.md"
        if log_path.exists():
            subprocess.run(
                ["git", "add", "editorial-log.md"],
                cwd=self.output_dir,
                capture_output=True,
            )
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=self.output_dir,
            capture_output=True,
            check=True,
        )


class EditorialLog:
    """Append-only editorial feedback log for workshop sessions."""

    def __init__(self, output_dir: Path, topic: str, brief: str):
        self.output_dir = output_dir
        self.file_path = output_dir / "editorial-log.md"

        frontmatter = {
            "topic": topic,
            "brief": brief,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None,
            "status": "active",
            "total_turns": 0,
        }
        content = "---\n" + yaml.dump(frontmatter, default_flow_style=False, sort_keys=False) + "---\n\n"
        content += f"# Editorial Log: {topic}\n"

        self.file_path.write_text(content)

    def append_feedback(self, turn_number: int, editor_name: str, feedback: str) -> None:
        section = f"\n\n## Turn {turn_number} — {editor_name} Review\n\n{feedback.strip()}\n"
        self._append(section)
        self._update_frontmatter(total_turns=turn_number)

    def append_referee_note(self, turn_number: int, note: str) -> None:
        comment = f"\n\n> **Referee @ Turn {turn_number}:** {note.strip()}\n"
        self._append(comment)

    def finalize(self, reason: str, total_turns: int) -> None:
        self._update_frontmatter(
            status=reason,
            ended_at=datetime.now(timezone.utc).isoformat(),
            total_turns=total_turns,
        )

    def read(self) -> str:
        return self.file_path.read_text()

    def _append(self, text: str) -> None:
        with open(self.file_path, "a") as f:
            f.write(text)

    def _update_frontmatter(self, **updates: object) -> None:
        text = self.file_path.read_text()
        match = re.match(r"^---\n(.*?\n)---\n", text, re.DOTALL)
        if not match:
            return

        fm = yaml.safe_load(match.group(1))
        fm.update(updates)
        new_fm = "---\n" + yaml.dump(fm, default_flow_style=False, sort_keys=False) + "---\n"
        new_text = new_fm + text[match.end():]
        self.file_path.write_text(new_text)
