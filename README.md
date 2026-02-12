# Discourse

**Multiple Claude agents, one document, plain files.**

Discourse is a multi-agent document generator. You define roles in a YAML config, and Discourse orchestrates multiple Claude Code sessions that take turns writing, critiquing, and revising a shared document. The document is the persistent artifact. The file system is the coordination layer. A human referee steers.

No frameworks, no vector databases, no agent memory systems. Just Claude sessions, markdown files, and a turn loop.

## The Idea

Most "multi-agent" systems are complicated. Discourse is not. The core loop is:

1. Agent A writes (or argues a position).
2. Agent B reviews (or argues the opposite).
3. The output is written to a file.
4. A human checks in periodically to steer, inject context, or stop.
5. Repeat.

Each agent is an independent Claude Code session with its own role and system prompt. They share state through the document itself — the same way human collaborators share state through a Google Doc. There's no shared memory, no message bus, no orchestration framework. The file *is* the coordination mechanism.

## Two Modes

### Debate

Two agents argue opposing positions on a topic. The output is a single conversation file — a structured back-and-forth with closing statements.

Good for: exploring tradeoffs, stress-testing ideas, generating balanced analysis of contentious topics.

```bash
discourse configs/example.yaml
```

### Workshop

An author agent writes a document. An editor agent reviews it. They iterate until the editor approves or you stop them. The document is git-versioned — every revision is a commit.

Good for: generating technical docs, guides, design documents, or any writing that benefits from an editorial loop.

```bash
discourse configs/workshop-example.yaml
```

## Installation

```bash
pip install -e .
```

Requires the [`claude` CLI](https://docs.anthropic.com/en/docs/claude-code) to be installed and authenticated. Without it, this is just a YAML validator.

## Quick Start

```bash
# Validate config without spending tokens
discourse configs/example.yaml --dry-run

# Run a debate
discourse configs/example.yaml

# Run a workshop
discourse configs/workshop-example.yaml
```

## Configuration

### Debate Config

```yaml
topic: "Tabs vs spaces: the final showdown"

participants:
  a:
    name: "Tab Enjoyer"
    role: |
      You are a grizzled systems programmer who insists tabs are
      objectively superior. You have strong opinions about accessibility,
      configurability, and file size.
  b:
    name: "Space Enthusiast"
    role: |
      You are a frontend developer who believes spaces are the only
      civilized choice. Consistency, readability, and PEP 8 are
      your weapons.

max_turns: 10          # Total turns across both participants
check_in_interval: 4   # Referee check-in every N turns
turn_timeout: 300      # Seconds before a turn times out
output_dir: "./conversations"
```

### Workshop Config

```yaml
mode: workshop
topic: "API Design Guide for REST Services"
brief: |
  Write a comprehensive guide for designing REST APIs.
  Cover: naming conventions, versioning, error formats, auth, pagination.
  Audience: backend engineers. Length: 1500-2500 words.

participants:
  author:
    name: "Technical Writer"
    role: |
      Experienced technical writer specializing in API documentation.
      You write clear, practical prose with concrete examples.
  editor:
    name: "Senior Architect"
    role: |
      Senior software architect with 15+ years building production APIs.
      You review for technical accuracy, completeness, and practical
      usefulness.

max_turns: 20
check_in_interval: 6
source_file: "./seed-document.md"  # Optional: start from existing content
```

Only `topic` and `participants` are required. Everything else has sensible defaults.

## The Referee

You're not a passive observer. Discourse gives you control over the process.

### Scheduled Check-ins

Every `check_in_interval` turns, you're asked what to do:

```
=== CHECK-IN (Turn 4/10) ===
[c] Continue
[s] Stop — collect closing statements and end
[m] Add a message to the conversation
[v] View document (workshop mode)
>
```

Use messages to steer the discussion, challenge weak arguments, or refocus a drifting workshop.

### On-Demand Questions

Agents can ask you questions mid-turn:

```markdown
<!-- REFEREE: Should we limit scope to REST, or include GraphQL? -->
```

Your answer gets injected into the conversation for both agents to see.

## Output

Each session creates a timestamped directory:

```
conversations/20260209T143000Z-tabs-vs-spaces/
  config.yaml          # Config snapshot
  sessions.json        # Claude session IDs
  audit.jsonl          # Event log (every invocation, token counts, costs)

  # Debate mode:
  conversation.md      # Full conversation with YAML frontmatter

  # Workshop mode:
  document.md          # The document (git-versioned)
  editorial-log.md     # Chronological editor feedback
  .git/                # Revision history
```

### Audit Trail

Every session produces an append-only JSONL log capturing each Claude invocation, token usage, costs, errors, referee interactions, and timing. Machine-readable, crash-safe, useful for understanding what happened and what it cost.

## Error Handling

When a Claude invocation fails:

```
ERROR during Turn 3 (Tab Enjoyer):
  claude CLI exited with code 1
[r]etry / [s]kip this turn / [a]bort
```

Ctrl+C at any point finalizes the conversation with an "interrupted" status. The output files are always valid — you never lose completed turns.

## CLI

```
Usage: discourse [OPTIONS] CONFIG_FILE

Options:
  --dry-run       Validate config and exit
  --output-dir    Override the config's output directory
  --help          Show this message and exit
```

## Project Structure

```
discourse/
  main.py            # CLI entrypoint (Click)
  orchestrator.py    # Debate mode turn loop + referee logic
  workshop.py        # Workshop mode turn loop + editorial flow
  conversation.py    # Config parsing + markdown conversation management
  document.py        # Workshop document + editorial log + git versioning
  claude.py          # Claude CLI wrapper (invoke, resume, error handling)
  audit.py           # JSONL audit trail
configs/
  example.yaml           # Monorepo vs polyrepo debate
  ide-debate.yaml        # IDE selection debate
  workshop-example.yaml  # REST API design guide workshop
conversations/           # Output directory (gitignored)
```

## Limitations

- **Context growth.** The full conversation is passed each turn. Long sessions will eventually hit context limits. 10-20 turns is the sweet spot.
- **No resume.** If the process exits, you can't pick up where you left off. Session IDs are saved but there's no resume command yet.
- **Text only.** Agents run in `bypassPermissions` mode — they generate text, nothing else. No tool use, no file system access.

## Design Decisions

**Why plain files?** Markdown is human-readable, diffable, and versionable. No database means no database problems. The file system is the simplest coordination mechanism that works.

**Why separate sessions?** Each agent maintains its own Claude session with `--resume`, preserving its conversation context across turns. They don't share memory — they share the document, the way human collaborators do.

**Why a human in the loop?** Unattended agent loops are a token furnace. Scheduled check-ins keep sessions on track and give you the ability to steer, challenge, or stop. The referee role is a feature, not a limitation.

**Why no agent framework?** The entire orchestration is a `while` loop that alternates between two `subprocess.run` calls. Adding LangChain or CrewAI or AutoGen to this would be like hiring a general contractor to hang a picture frame.
