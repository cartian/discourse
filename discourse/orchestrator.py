from __future__ import annotations

import subprocess
from pathlib import Path

import click

from .claude import InvokeResult, invoke_claude, handle_error, check_referee_request
from .conversation import Config, Conversation


SYSTEM_PROMPT_TEMPLATE = """You are "{participant_name}" in a structured discourse.

Your role: {role_description}

Rules:
- Stay in character and argue your position
- Engage directly with the other participant's points
- Be concise but substantive (aim for 200-400 words per turn)
- If you need input from the human referee, include: <!-- REFEREE: your question here -->
- When asked for a closing statement, summarize your key arguments and any concessions"""

TURN_PROMPT_TEMPLATE = """The conversation so far:

{conversation_content}

---

Write your response for Turn {turn_number}. Output ONLY your response content — no headers, no metadata."""

CLOSING_PROMPT_TEMPLATE = """The conversation so far:

{conversation_content}

---

The discourse has concluded. Write your closing statement. Summarize your key arguments, acknowledge any strong points from your opponent, and note any concessions you'd make. Output ONLY your closing statement — no headers, no metadata."""


class Orchestrator:
    def __init__(self, config: Config, output_dir: str | None = None):
        self.config = config
        self.conversation = Conversation(config, output_dir=output_dir)
        self.sessions: dict[str, str | None] = {"a": None, "b": None}

    def run(self) -> Path:
        """Execute the full discourse loop."""
        click.echo(f"Topic: {self.config.topic}")
        click.echo(f"Participants: {self.config.participants['a'].name} vs {self.config.participants['b'].name}")
        click.echo(f"Max turns: {self.config.max_turns}, Check-in every {self.config.check_in_interval} turns")
        click.echo()

        file_path = self.conversation.init()
        click.echo(f"Conversation file: {file_path}")
        click.echo()

        try:
            self._run_turns()
            closing = self._collect_closing_statements()
            self.conversation.finalize("completed", closing)
        except KeyboardInterrupt:
            click.echo("\n\nInterrupted! Finalizing conversation...")
            self.conversation.finalize("interrupted")
        except SystemExit:
            click.echo("\nAborted! Finalizing conversation...")
            self.conversation.finalize("aborted")

        click.echo(f"\nConversation saved to: {self.conversation.file_path}")
        click.echo(f"Total turns: {self.conversation.total_turns}")
        return self.conversation.file_path

    def _run_turns(self) -> None:
        for turn in range(1, self.config.max_turns + 1):
            speaker_key = "a" if turn % 2 == 1 else "b"
            participant = self.config.participants[speaker_key]

            click.echo(f"--- Turn {turn}/{self.config.max_turns}: {participant.name} ---")

            response_text = self._invoke_turn(turn, speaker_key)
            if response_text is None:
                continue  # skipped

            # Check for referee request from participant
            cleaned_text, referee_question = check_referee_request(response_text)
            if referee_question:
                click.echo(f"\n{participant.name} asks the referee:")
                click.echo(f"  {referee_question}")
                answer = click.prompt("Referee response")
                self.conversation.append_referee_note(turn, answer)
                response_text = cleaned_text

            self.conversation.append_turn(turn, participant.name, response_text)
            click.echo(f"  Turn {turn}/{self.config.max_turns} — {participant.name} responded")

            # Scheduled check-in
            if turn % self.config.check_in_interval == 0 and turn < self.config.max_turns:
                if not self._check_in(turn):
                    break

    def _invoke_turn(self, turn: int, speaker_key: str) -> str | None:
        """Invoke claude for a single turn with retry logic. Returns text or None if skipped."""
        participant = self.config.participants[speaker_key]
        conversation_content = self.conversation.read()

        prompt = TURN_PROMPT_TEMPLATE.format(
            conversation_content=conversation_content,
            turn_number=turn,
        )

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            participant_name=participant.name,
            role_description=participant.role,
        )

        while True:
            try:
                # Use system prompt on first turn, resume on subsequent
                if self.sessions[speaker_key] is None:
                    result = invoke_claude(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        timeout=self.config.turn_timeout,
                    )
                else:
                    result = invoke_claude(
                        prompt=prompt,
                        session_id=self.sessions[speaker_key],
                        timeout=self.config.turn_timeout,
                    )
                self.sessions[speaker_key] = result.session_id
                return result.text

            except (subprocess.TimeoutExpired, RuntimeError) as e:
                action = handle_error(turn, participant.name, e)
                if action == "retry":
                    continue
                elif action == "skip":
                    self.conversation.append_turn(
                        turn, participant.name, "*(Turn skipped due to error.)*"
                    )
                    return None

    def _check_in(self, turn: int) -> bool:
        """Pause for referee check-in. Returns True to continue, False to stop."""
        click.echo(f"\n{'='*50}")
        click.echo(f"=== CHECK-IN (Turn {turn}/{self.config.max_turns}) ===")
        click.echo(f"{'='*50}")

        choice = click.prompt(
            "[c] Continue  [s] Stop — collect closing statements  [m] Add a message",
            type=click.Choice(["c", "s", "m"], case_sensitive=False),
        )

        if choice == "c":
            return True
        elif choice == "s":
            return False
        elif choice == "m":
            message = click.prompt("Referee message")
            self.conversation.append_referee_note(turn, message)
            click.echo("  Message added to conversation.")
            return True

        return True

    def _collect_closing_statements(self) -> dict[str, str]:
        """Invoke each participant one more time for closing statements."""
        click.echo("\n--- Collecting closing statements ---")
        statements: dict[str, str] = {}
        conversation_content = self.conversation.read()

        for key in ("a", "b"):
            participant = self.config.participants[key]
            click.echo(f"  Requesting closing statement from {participant.name}...")

            prompt = CLOSING_PROMPT_TEMPLATE.format(
                conversation_content=conversation_content,
            )

            try:
                if self.sessions[key] is not None:
                    result = invoke_claude(
                        prompt=prompt,
                        session_id=self.sessions[key],
                        timeout=self.config.turn_timeout,
                    )
                else:
                    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
                        participant_name=participant.name,
                        role_description=participant.role,
                    )
                    result = invoke_claude(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        timeout=self.config.turn_timeout,
                    )
                statements[key] = result.text
                click.echo(f"  {participant.name} — done")
            except (subprocess.TimeoutExpired, RuntimeError) as e:
                click.echo(f"  Warning: Could not get closing statement from {participant.name}: {e}")
                statements[key] = "*(Closing statement could not be collected due to an error.)*"

        return statements
