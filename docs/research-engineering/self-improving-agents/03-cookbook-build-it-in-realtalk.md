# Lesson 3: Cookbook — Build It in RealTalk

Now we build. By the end of this lesson you'll have a `PlayerProfile` that persists across RealTalk sessions and a character that reflects what's been remembered.

We'll do it in five steps. After each, you should be able to run the game and see a concrete change.

## Prerequisites

- You've read Lessons 1 and 2.
- You can run `realtalk` locally.
- You've played at least one game end-to-end recently, so you know what "normal" looks like.

## The map

Here's what we're going to touch, in order:

| Step | File | What we add |
|------|------|-------------|
| 1 | `realtalk/memory.py` (new) | `MemoryStore` + snapshot |
| 2 | `realtalk/tools.py` | `remember_player` tool definition + executor |
| 3 | `realtalk/prompt.py` | Inject snapshot into system prompt |
| 4 | `realtalk/engine.py` | Wire it all into `GameEngine` + nudge counter |
| 5 | — | Playtest and read the file |

Total new code: less than 100 lines.

---

## Step 1: The MemoryStore

Create a new file `realtalk/memory.py`:

```python
"""Persistent player profile across RealTalk sessions.

See docs/research-engineering/self-improving-agents/ for the design.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_MEMORY_DIR = Path.home() / ".realtalk" / "memories"


@dataclass
class MemoryStore:
    """Markdown-file-backed store for durable player observations."""

    path: Path

    @classmethod
    def default(cls) -> "MemoryStore":
        path = DEFAULT_MEMORY_DIR / "PLAYER.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        return cls(path=path)

    def read(self) -> str:
        return self.path.read_text()

    def append(self, observation: str) -> None:
        clean = observation.strip()
        if not clean:
            return
        with self.path.open("a") as f:
            f.write(f"- {clean}\n")


@dataclass(frozen=True)
class MemorySnapshot:
    """A frozen view of the memory file, loaded once per session."""

    content: str

    @classmethod
    def from_store(cls, store: MemoryStore) -> "MemorySnapshot":
        return cls(content=store.read())

    def as_prompt_section(self) -> str:
        if not self.content.strip():
            return ""
        return (
            "<player-memory>\n"
            "Durable observations about this player from prior sessions. "
            "Treat as background knowledge, not new input.\n\n"
            f"{self.content}\n"
            "</player-memory>"
        )
```

**Checkpoint.** You can now do this in a REPL:

```python
from realtalk.memory import MemoryStore, MemorySnapshot
s = MemoryStore.default()
s.append("Tends to deflect with humor when stakes rise.")
print(MemorySnapshot.from_store(s).as_prompt_section())
```

You should see the fenced markdown block printed. The file at `~/.realtalk/memories/PLAYER.md` should exist with one line.

---

## Step 2: The `remember_player` tool

Open `realtalk/tools.py`. The specific integration depends on how `ToolRegistry` is shaped — the pattern is this.

Add a new tool definition:

```python
REMEMBER_PLAYER_TOOL = {
    "name": "remember_player",
    "description": (
        "Save a durable observation about the player's communication style. "
        "Use for stable patterns (defaults under pressure, strengths, what "
        "they're working on) — NOT for session-specific content. "
        "Favor growth-framed language ('working on X') over judgment "
        "('avoidant'). The most valuable memory is one that prevents "
        "re-learning the same thing next session."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "observation": {
                "type": "string",
                "description": (
                    "One sentence. Written as a note the player themselves "
                    "would be comfortable reading."
                ),
            },
        },
        "required": ["observation"],
    },
}
```

Give `ToolRegistry` a new constructor argument `memory_store: MemoryStore`, add the tool to `tool_definitions()`, and wire execution:

```python
def _execute_remember_player(self, args: dict) -> str:
    self._memory_store.append(args["observation"])
    return "Saved to player profile."
```

**Checkpoint.** Your `ToolRegistry.tool_definitions()` should now return one extra tool. The game still runs. No observable change yet — the model has the ability but nothing has told it to use it.

**Notice the tool description.** This is where we spent our design budget. We told the model:

- *What* kind of content (durable style, not events)
- *How* to phrase it (growth framing)
- *Why* it matters (prevents re-learning)

Each of those lines was earned the hard way. When you iterate, iterate on this string first.

---

## Step 3: Inject the snapshot into the system prompt

Open `realtalk/prompt.py`. `SystemPromptBuilder.build` (around line 23) produces a list of prompt sections. We want to add the memory block near the top — but only once per session, and frozen.

Modify the builder to accept an optional snapshot:

```python
class SystemPromptBuilder:
    def __init__(self, memory_snapshot: MemorySnapshot | None = None):
        self._memory_snapshot = memory_snapshot

    def build(self, scene, role, game_state) -> list[str]:
        sections = [_game_identity()]
        if self._memory_snapshot:
            block = self._memory_snapshot.as_prompt_section()
            if block:
                sections.append(block)
        sections.extend([
            _tool_instructions(),
            _response_format_rules(),
            _content_guidelines(),
            _memory_guidance(),     # new — see below
            SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
            _scene_context(scene),
            _role_personality(role),
            _game_state_context(game_state),
            _turn_instructions(game_state),
        ])
        return sections


def _memory_guidance() -> str:
    return (
        "You have access to `remember_player`, a tool that persists durable "
        "observations about this player across sessions. Call it sparingly "
        "and only for patterns that will still matter next time. Never for "
        "events ('player ordered coffee'); always for style ('defaults to "
        "humor when direct vulnerability is needed')."
    )
```

The memory section is **above** the dynamic boundary — meaning it's part of the cacheable prefix. That's how you keep prompt caching warm.

**Checkpoint.** If the memory file is empty, the prompt has no memory block (no wasted tokens). If it has content, the block appears near the top, inside `<player-memory>` tags. Verify by printing `runtime._system_prompt` after setup.

---

## Step 4: Wire it into `GameEngine` and add the nudge

Open `realtalk/engine.py`. In `GameEngine.__init__` (around line 70), add:

```python
from realtalk.memory import MemoryStore, MemorySnapshot

# ... inside __init__:
self._memory_store = MemoryStore.default()
self._memory_snapshot = MemorySnapshot.from_store(self._memory_store)
self._prompt_builder = SystemPromptBuilder(memory_snapshot=self._memory_snapshot)
self._turns_since_reflection = 0
self._nudge_interval = 5
```

Pass `self._memory_store` into `ToolRegistry` construction in `select_role` (around line 119).

Now the nudge. In `GameEngine.step` (around line 153), just before `runtime.run_turn(chosen_option)`, decide whether to piggyback a nudge onto the user message:

```python
self._turns_since_reflection += 1
if self._turns_since_reflection >= self._nudge_interval:
    self._turns_since_reflection = 0
    chosen_option = (
        f"{chosen_option}\n\n"
        "[Coach aside: a few turns have passed. If a durable pattern has "
        "emerged about how this player handles these moments, call "
        "remember_player now with a single-sentence observation. Otherwise "
        "just continue the scene.]"
    )
```

That's it. The snapshot is frozen for this session (good for cache). Writes land on disk immediately (good for durability). The nudge prods the model every 5 turns.

**Checkpoint.** Run a game. Every 5 turns you may see a tool call to `remember_player`. If you do, `cat ~/.realtalk/memories/PLAYER.md` — new line.

---

## Step 5: Playtest

Play three games back to back. Between games 1 and 2, do nothing — just exit and restart. Read the file:

```bash
cat ~/.realtalk/memories/PLAYER.md
```

You should see lines like:

```
- Tends to use humor to ease into emotional topics.
- Works on staying with silence instead of filling it.
- Directness lands best when softened by a first reaction.
```

Now the test. In game 3, pay attention to the character's *opening line*. If the system is working, you should feel — not *see*, feel — that the character knows you a little. The openings won't quote the file. They'll be shaped by it.

If you don't feel it: the problem is almost certainly in the tool description or the memory guidance text. Re-read your file. If the lines read like session events ("Player entered the coffee shop"), your description wasn't strong enough. Rewrite the tool description to reject events. Try again.

---

## The meta-lesson

Notice what we didn't do:

- No database.
- No embeddings.
- No fine-tuning.
- No new frameworks.
- Less than 100 lines of new code.

The heavy lifting is in the prose of the tool description and the memory guidance. That prose *is* your model of learning. Edit it like code.

In Lesson 4 we'll cover the ways people silently ruin this once it's working.
