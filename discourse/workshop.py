from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import click

from .audit import AuditLog
from .claude import invoke_claude, handle_error, check_referee_request
from .conversation import Config
from .document import Document, EditorialLog


AUTHOR_SYSTEM_PROMPT = """You are "{participant_name}" — a workshop author.

Your role: {role_description}

You are collaborating with an editor to produce a polished document. Your job is to write and revise.

Rules:
- When writing a first draft, follow the brief closely
- When revising, make surgical changes that address the editor's specific feedback
- Preserve what works — don't rewrite sections the editor praised
- Output the COMPLETE document every time (it will replace the previous version)
- Do not include meta-commentary about your changes — just output the document
- If you need input from the human referee, include: <!-- REFEREE: your question here -->"""

AUTHOR_INITIAL_PROMPT = """Write the first draft of this document.

**Brief:**
{brief}

Output ONLY the document content (markdown). No preamble, no meta-commentary."""

AUTHOR_REVISION_PROMPT = """Here is the current document:

---
{document}
---

The editor provided this feedback:

---
{feedback}
---

Revise the document to address the editor's feedback. Make targeted changes — preserve what works.
Output the COMPLETE revised document. No preamble, no meta-commentary."""

EDITOR_SYSTEM_PROMPT = """You are "{participant_name}" — a workshop editor.

Your role: {role_description}

You are reviewing a document that was written to fulfill a specific brief. Your job is to provide constructive, actionable feedback.

Your review MUST use this structure:

**Assessment:** 1-2 sentence overall evaluation.

**Strengths:** What works well (bullet points).

**Suggestions:** Specific, actionable changes (bullet points). Reference sections or lines.

**Questions:** Any clarifying questions for the author (bullet points). Omit if none.

**Verdict:** One of:
- `REVISE` — the document needs changes (default)
- `APPROVED` — the document meets the brief and is ready for publication

Rules:
- Be specific — "tighten the introduction" is better than "needs work"
- Balance praise and criticism — acknowledge what works
- Refer to specific sections when suggesting changes
- Only use APPROVED when the document genuinely meets the brief
- If you need input from the human referee, include: <!-- REFEREE: your question here -->"""

EDITOR_REVIEW_PROMPT = """Review this document against the original brief.

**Brief:**
{brief}

**Document:**

---
{document}
---

Provide your structured review. Remember to include a Verdict (REVISE or APPROVED)."""


class WorkshopOrchestrator:
    def __init__(self, config: Config, output_dir: str | None = None):
        self.config = config
        base_dir = Path(output_dir or config.output_dir)
        base_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = re.sub(r"[^a-z0-9]+", "-", config.topic.lower()).strip("-")[:60]
        self.session_dir = base_dir / f"{timestamp}-{slug}"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        if config.source_path and config.source_path.is_file():
            shutil.copy2(config.source_path, self.session_dir / "config.yaml")

        self.document = Document(self.session_dir, config.topic, config.source_file)
        self.log = EditorialLog(self.session_dir, config.topic, config.brief)
        self.audit = AuditLog(self.session_dir)
        self.sessions: dict[str, str | None] = {"author": None, "editor": None}
        self.total_turns = 0

    def run(self) -> Path:
        author = self.config.participants["author"]
        editor = self.config.participants["editor"]

        click.echo(f"Workshop: {self.config.topic}")
        click.echo(f"  Author: {author.name}")
        click.echo(f"  Editor: {editor.name}")
        click.echo(f"  Max turns: {self.config.max_turns}, Check-in every {self.config.check_in_interval} turns")
        click.echo(f"  Session: {self.session_dir}")
        click.echo()

        self.audit.log_session_start(
            mode="workshop",
            topic=self.config.topic,
            participants={
                k: {"name": v.name, "role": v.role}
                for k, v in self.config.participants.items()
            },
            config={
                "max_turns": self.config.max_turns,
                "check_in_interval": self.config.check_in_interval,
                "turn_timeout": self.config.turn_timeout,
            },
        )

        status = "completed"
        try:
            self._run_workshop_loop()
            self.log.finalize("completed", self.total_turns)
        except KeyboardInterrupt:
            status = "interrupted"
            click.echo("\n\nInterrupted! Finalizing...")
            self.log.finalize("interrupted", self.total_turns)
        except SystemExit:
            status = "aborted"
            click.echo("\nAborted! Finalizing...")
            self.log.finalize("aborted", self.total_turns)
        finally:
            self.audit.log_session_end(status, self.total_turns)
            self.audit.close()

        click.echo(f"\nDocument: {self.document.file_path}")
        click.echo(f"Editorial log: {self.log.file_path}")
        click.echo(f"Total turns: {self.total_turns}")
        click.echo(f"Git history: git -C {self.session_dir} log --oneline")
        return self.session_dir

    def _run_workshop_loop(self) -> None:
        author = self.config.participants["author"]
        editor = self.config.participants["editor"]

        # Turn 1: Author writes initial draft
        self.total_turns = 1
        click.echo(f"--- Turn 1/{self.config.max_turns}: {author.name} (initial draft) ---")

        draft = self._invoke_author_initial()
        if draft is None:
            return

        draft = self._handle_referee(draft)
        self.document.write(draft, 1)
        click.echo(f"  Initial draft written and committed.")

        # Editor/Author loop — each iteration is one editor+author pair
        turn = 1
        while turn < self.config.max_turns:
            # --- Editor review ---
            turn += 1
            self.total_turns = turn

            click.echo(f"--- Turn {turn}/{self.config.max_turns}: {editor.name} (review) ---")
            feedback = self._invoke_editor()
            if feedback is not None:
                feedback = self._handle_referee(feedback)
                self.log.append_feedback(turn, editor.name, feedback)
                click.echo(f"  Review recorded.")

                # Check for APPROVED verdict
                if self._is_approved(feedback):
                    click.echo(f"\n  {editor.name} verdict: APPROVED")
                    click.echo("  Workshop complete.")
                    return
            else:
                click.echo(f"  Editor turn skipped.")
                feedback = None

            # Check-in at intervals
            if turn % self.config.check_in_interval == 0 and turn < self.config.max_turns:
                if self._check_in(turn) == "stop":
                    return

            # --- Author revision ---
            turn += 1
            self.total_turns = turn
            if turn > self.config.max_turns:
                break

            if feedback is not None:
                click.echo(f"--- Turn {turn}/{self.config.max_turns}: {author.name} (revision) ---")
                revision = self._invoke_author_revision(feedback)
                if revision is not None:
                    revision = self._handle_referee(revision)
                    self.document.write(revision, turn)
                    click.echo(f"  Revision committed.")
                else:
                    click.echo(f"  Author turn skipped.")
            else:
                # No feedback to revise against — skip author turn
                click.echo(f"--- Turn {turn}/{self.config.max_turns}: {author.name} (skipped — no feedback) ---")

            # Check-in at intervals
            if turn % self.config.check_in_interval == 0 and turn < self.config.max_turns:
                if self._check_in(turn) == "stop":
                    return

        click.echo(f"\nWorkshop ended after {self.total_turns} turns (max: {self.config.max_turns}).")

    def _invoke_author_initial(self) -> str | None:
        author = self.config.participants["author"]
        prompt = AUTHOR_INITIAL_PROMPT.format(brief=self.config.brief)
        system_prompt = AUTHOR_SYSTEM_PROMPT.format(
            participant_name=author.name,
            role_description=author.role,
        )
        return self._invoke_with_retry(1, "author", prompt, system_prompt)

    def _invoke_author_revision(self, feedback: str) -> str | None:
        prompt = AUTHOR_REVISION_PROMPT.format(
            document=self.document.read(),
            feedback=feedback,
        )
        return self._invoke_with_retry(self.total_turns, "author", prompt)

    def _invoke_editor(self) -> str | None:
        prompt = EDITOR_REVIEW_PROMPT.format(
            brief=self.config.brief,
            document=self.document.read(),
        )
        editor = self.config.participants["editor"]
        system_prompt = EDITOR_SYSTEM_PROMPT.format(
            participant_name=editor.name,
            role_description=editor.role,
        )
        return self._invoke_with_retry(self.total_turns, "editor", prompt, system_prompt if self.sessions["editor"] is None else None)

    def _invoke_with_retry(self, turn: int, role_key: str, prompt: str, system_prompt: str | None = None) -> str | None:
        participant = self.config.participants[role_key]
        self.audit.log_turn_start(turn, role_key, participant.name)

        while True:
            is_new_session = self.sessions[role_key] is None
            effective_system_prompt = system_prompt

            try:
                if self.sessions[role_key] is None and system_prompt:
                    result = invoke_claude(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        timeout=self.config.turn_timeout,
                    )
                elif self.sessions[role_key] is not None:
                    result = invoke_claude(
                        prompt=prompt,
                        session_id=self.sessions[role_key],
                        timeout=self.config.turn_timeout,
                    )
                    effective_system_prompt = None
                else:
                    # First call but no system prompt provided — create system prompt
                    sp = AUTHOR_SYSTEM_PROMPT if role_key == "author" else EDITOR_SYSTEM_PROMPT
                    sp = sp.format(
                        participant_name=participant.name,
                        role_description=participant.role,
                    )
                    effective_system_prompt = sp
                    result = invoke_claude(
                        prompt=prompt,
                        system_prompt=sp,
                        timeout=self.config.turn_timeout,
                    )

                self.sessions[role_key] = result.session_id
                self._save_sessions()
                self.audit.log_invoke(
                    turn=turn,
                    participant_key=role_key,
                    result=result,
                    prompt=prompt,
                    system_prompt=effective_system_prompt,
                    is_new_session=is_new_session,
                )
                return result.text

            except (subprocess.TimeoutExpired, RuntimeError) as e:
                action = handle_error(turn, participant.name, e)
                self.audit.log_error(turn, role_key, participant.name, e, action)
                if action == "retry":
                    continue
                elif action == "skip":
                    return None

    def _handle_referee(self, text: str) -> str:
        """Check for referee request markers and handle interactively."""
        cleaned, question = check_referee_request(text)
        if question:
            click.echo(f"\n  Participant asks the referee:")
            click.echo(f"    {question}")
            answer = click.prompt("  Referee response")
            self.log.append_referee_note(self.total_turns, answer)
            self.audit.log_referee(self.total_turns, question, answer)
            return cleaned
        return text

    def _save_sessions(self) -> None:
        """Write current session IDs to sessions.json."""
        path = self.session_dir / "sessions.json"
        data = {k: v for k, v in self.sessions.items() if v is not None}
        path.write_text(json.dumps(data, indent=2) + "\n")

    def _is_approved(self, feedback: str) -> bool:
        return bool(re.search(r"\bVerdict:\s*APPROVED\b", feedback, re.IGNORECASE))

    def _check_in(self, turn: int) -> str:
        click.echo(f"\n{'='*50}")
        click.echo(f"=== CHECK-IN (Turn {turn}/{self.config.max_turns}) ===")
        click.echo(f"{'='*50}")

        choice = click.prompt(
            "[c] Continue  [s] Stop  [m] Add a message  [v] View document",
            type=click.Choice(["c", "s", "m", "v"], case_sensitive=False),
        )

        if choice == "c":
            self.audit.log_check_in(turn, "continue")
            return "continue"
        elif choice == "s":
            self.audit.log_check_in(turn, "stop")
            return "stop"
        elif choice == "v":
            click.echo(f"\n--- Document ---\n")
            click.echo(self.document.read())
            click.echo(f"\n--- End ---\n")
            # Re-prompt after viewing
            return self._check_in(turn)
        elif choice == "m":
            message = click.prompt("Referee message")
            self.log.append_referee_note(turn, message)
            self.audit.log_check_in(turn, "message", message)
            click.echo("  Message added to editorial log.")
            return "continue"

        return "continue"
