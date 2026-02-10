from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml


@dataclass
class Participant:
    name: str
    role: str


VALID_MODES = {"debate", "workshop"}
PARTICIPANT_KEYS = {
    "debate": ("a", "b"),
    "workshop": ("author", "editor"),
}


@dataclass
class Config:
    topic: str
    participants: dict[str, Participant]
    mode: str = "debate"
    brief: str | None = None
    source_file: str | None = None
    max_turns: int = 10
    check_in_interval: int = 4
    turn_timeout: int = 300
    output_dir: str = "./conversations"
    source_path: Path | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
        with open(path) as f:
            data = yaml.safe_load(f)

        mode = data.get("mode", "debate")
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of: {', '.join(VALID_MODES)}")

        if not data.get("topic"):
            raise ValueError("Config must include a 'topic'")

        expected_keys = PARTICIPANT_KEYS[mode]
        if not data.get("participants") or len(data["participants"]) != 2:
            raise ValueError(f"Config must include exactly 2 participants ({', '.join(expected_keys)})")

        participants = {}
        for key in expected_keys:
            p = data["participants"].get(key)
            if not p or not p.get("name") or not p.get("role"):
                raise ValueError(f"Participant '{key}' must have 'name' and 'role'")
            participants[key] = Participant(name=p["name"], role=p["role"].strip())

        if mode == "workshop" and not data.get("brief"):
            raise ValueError("Workshop mode requires a 'brief' field")

        return cls(
            topic=data["topic"],
            participants=participants,
            mode=mode,
            brief=data.get("brief"),
            source_file=data.get("source_file"),
            max_turns=data.get("max_turns", 10),
            check_in_interval=data.get("check_in_interval", 4),
            turn_timeout=data.get("turn_timeout", 300),
            output_dir=data.get("output_dir", "./conversations"),
            source_path=Path(path).resolve(),
        )


class Conversation:
    def __init__(self, config: Config, output_dir: str | None = None):
        self.config = config
        base_dir = Path(output_dir or config.output_dir)
        base_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = re.sub(r"[^a-z0-9]+", "-", config.topic.lower()).strip("-")[:60]
        self.session_dir = base_dir / f"{timestamp}-{slug}"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.session_dir / "conversation.md"

        if config.source_path and config.source_path.is_file():
            shutil.copy2(config.source_path, self.session_dir / "config.yaml")

        self.started_at = datetime.now(timezone.utc).isoformat()
        self.total_turns = 0

    def init(self) -> Path:
        """Create the conversation file with frontmatter and heading."""
        frontmatter = {
            "topic": self.config.topic,
            "started_at": self.started_at,
            "ended_at": None,
            "status": "active",
            "total_turns": 0,
            "participants": {
                k: v.name for k, v in self.config.participants.items()
            },
        }
        content = "---\n" + yaml.dump(frontmatter, default_flow_style=False, sort_keys=False) + "---\n\n"
        content += f"# Discourse: {self.config.topic}\n"

        self.file_path.write_text(content)
        return self.file_path

    def append_turn(self, turn_number: int, participant_name: str, content: str) -> None:
        """Append a turn to the conversation file."""
        self.total_turns = turn_number
        section = f"\n\n## Turn {turn_number} - {participant_name}\n\n{content.strip()}\n"
        self._append(section)
        self._update_frontmatter(total_turns=turn_number)

    def append_referee_note(self, turn_number: int, note: str) -> None:
        """Insert a referee comment into the conversation file."""
        comment = f"\n\n<!-- REFEREE @ Turn {turn_number}: {note.strip()} -->\n"
        self._append(comment)

    def finalize(self, reason: str, closing_statements: dict[str, str] | None = None) -> None:
        """Write closing section and update frontmatter status."""
        parts = ["\n\n---\n\n## Closing Statements\n"]

        if closing_statements:
            for key in ("a", "b"):
                name = self.config.participants[key].name
                statement = closing_statements.get(key, "(no closing statement)")
                parts.append(f"\n### {name}\n\n{statement.strip()}\n")
        else:
            parts.append("\n*(Closing statements were not collected.)*\n")

        self._append("".join(parts))
        self._update_frontmatter(
            status=reason,
            ended_at=datetime.now(timezone.utc).isoformat(),
        )

    def read(self) -> str:
        """Return current file contents."""
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
