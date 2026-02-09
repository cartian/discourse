# Discourse

**A turn-based conversation orchestrator for Claude.**

Ever wanted to watch two AI sessions argue about monorepos while you sit back and eat popcorn? Now you can. Discourse sets up a structured debate between two Claude Code sessions, managed through a shared markdown file, with you as the all-powerful referee.

## How It Works

1. You write a config file defining a topic and two participants with opposing roles.
2. Discourse spawns two independent Claude sessions and has them take turns responding.
3. The conversation is written to a markdown file in real time.
4. Every few turns, you get a check-in where you can nudge the conversation, inject commentary, or mercifully end it.

The participants don't know they're talking to another Claude. They just see a growing conversation and are told to engage with the other side's points.

## Installation

```bash
cd ~/Projects/vibe-coding/discourse
pip install -e .
```

Requires the `claude` CLI to be installed and authenticated. If you don't have that, this tool is just a very elaborate YAML validator.

## Quick Start

```bash
# Validate your config without burning any tokens
discourse configs/example.yaml --dry-run

# Let the debate begin
discourse configs/example.yaml
```

Or run it as a module:

```bash
python -m discourse.main configs/example.yaml
```

## Writing a Config

Configs are YAML files. Here's the anatomy:

```yaml
topic: "Tabs vs spaces: the final showdown"

participants:
  a:
    name: "Tab Enjoyer"
    role: |
      You are a grizzled systems programmer who insists tabs are
      objectively superior. You have strong opinions about accessibility,
      configurability, and file size. You will not be moved.
  b:
    name: "Space Enthusiast"
    role: |
      You are a frontend developer who believes spaces are the only
      civilized choice. Consistency, readability, and the PEP 8 spec
      are your weapons. You type with conviction.

max_turns: 10 # Total turns across both participants
check_in_interval: 4 # Referee check-in every N turns
turn_timeout: 300 # Seconds before a turn times out
output_dir: "./conversations"
```

Only `topic` and `participants` are required. Everything else has sensible defaults (shown above).

## Being the Referee

### Scheduled Check-ins

Every `check_in_interval` turns, Discourse pauses and asks what you want to do:

```
=== CHECK-IN (Turn 4/10) ===
[c] Continue
[s] Stop — collect closing statements and end
[m] Add a message to the conversation
>
```

- **Continue** — let them keep going.
- **Stop** — each participant gives a closing statement, then the file is finalized.
- **Message** — inject a referee note into the conversation. Both participants will see it on subsequent turns. Use this to steer the discussion ("Focus on testing strategies") or stir the pot ("Neither of you has mentioned Makefiles yet").

### On-Demand Referee Requests

Participants can ask you questions mid-turn by including a special marker in their response:

```markdown
<!-- REFEREE: Are we allowed to discuss build systems outside of Bazel? -->
```

When this happens, Discourse will show you the question and prompt for an answer. Your response gets added to the conversation as a referee note visible to both sides.

## Output

Conversations are saved as markdown files in the output directory with timestamped filenames:

```
conversations/20260209T143000Z-tabs-vs-spaces-the-final-showdown.md
```

The file includes YAML frontmatter with metadata, the full turn-by-turn conversation, referee notes as HTML comments, and closing statements from both participants.

## Error Handling

Things go wrong sometimes. When a Claude invocation fails (timeout, bad exit code, unparseable output), you get a choice:

```
ERROR during Turn 3 (Tab Enjoyer):
  claude CLI exited with code 1
  stderr: ...
[r]etry / [s]kip this turn / [a]bort
```

If you hit Ctrl+C at any point, Discourse gracefully finalizes the conversation with an "interrupted" status. No closing statements, but the file is still valid markdown with everything up to that point.

## CLI Options

```
Usage: discourse [OPTIONS] CONFIG_FILE

  Run a structured discourse between two Claude sessions.

Options:
  --dry-run       Validate config and exit
  --output-dir    Override the config's output directory
  --help          Show this message and exit
```

## Project Structure

```
discourse/
  __init__.py
  main.py            # Click CLI entrypoint
  conversation.py    # Config loading + markdown file management
  orchestrator.py    # Claude CLI invocation + turn loop + referee logic
configs/
  example.yaml       # Monorepo vs polyrepo debate
conversations/       # Output directory (gitignored)
```

## Known Limitations

- Participants share the full conversation as context each turn, so very long debates will eventually hit context limits. Keep `max_turns` reasonable (10-20 is a good range).
- There's no way to resume a conversation after the process exits. If you want to continue a debate, you'll need to start fresh.
- Both participants use `--permission-mode bypassPermissions`, so they can't run tools or touch your filesystem. They can only talk.

## Philosophy

Sometimes the best way to explore a topic is to hear two well-argued sides go at it. Discourse gives you a structured way to do that with Claude — and the referee role means you're never just a passive observer. You're the one asking the hard follow-up questions, keeping things on track, and deciding when enough is enough.

It's pair programming, except the pair is arguing and you're the manager.
