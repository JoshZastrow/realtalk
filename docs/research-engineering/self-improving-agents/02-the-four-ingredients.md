# Lesson 2: The Four Ingredients

We claimed in Lesson 1 that a self-improving agent needs only four parts. Let's look at each one. For every ingredient: a one-line definition, the intuition, the minimum code, and the one mistake to avoid.

---

## Ingredient 1: The Memory File

**Definition:** A plain markdown file on disk that holds what the agent has learned about the player.

**Intuition.** Think of it like a sticky note the agent keeps on its monitor. Every time it learns something worth remembering, it writes a new line. When it starts work each day, it reads the whole note before picking up the keyboard.

**Minimum code:**

```python
from pathlib import Path

class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def read(self) -> str:
        return self.path.read_text()

    def append(self, line: str) -> None:
        with self.path.open("a") as f:
            f.write(line.rstrip() + "\n")
```

Thirty lines, counting blanks.

**Why it works.** All the properties we listed in Lesson 1 — inspectable, editable, cheap — come from choosing the most boring possible storage medium.

**Gotcha.** Two processes writing at once will interleave. If you're running a dev server and a test suite simultaneously, you need `filelock` or a mutex. Hermes uses `fcntl.flock` in `agent/memory_provider.py`. For RealTalk (one process, one player) you can skip this.

---

## Ingredient 2: The Memory Tool

**Definition:** A tool the LLM can call during a turn to append a line to the memory file.

**Intuition.** The model is the one who notices "this is worth remembering." It needs a way to act on that. Giving it a `remember` tool is the bridge from "thinking" to "writing down."

**Minimum code:**

```python
def remember_tool_definition():
    return {
        "name": "remember",
        "description": (
            "Save a durable observation about the player. "
            "Use for stable style patterns, not session-specific events. "
            "The most valuable memory is one that prevents having to "
            "re-learn the same thing next session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "observation": {"type": "string"},
            },
            "required": ["observation"],
        },
    }

def execute_remember(memory: MemoryStore, args: dict) -> str:
    memory.append(args["observation"])
    return "Saved."
```

**Why it works.** Tools are the API the model already knows how to use. You don't invent a new mechanism. You give it a verb.

**Gotcha.** The **description** of the tool is where the whole design lives. "Save anything interesting" will fill your file with noise ("Player said hello"). "Save durable observations that reduce future re-learning" will fill it with gold. Rewrite the description ten times. Watch what changes in the file.

This is the single highest-leverage string of text in your system. Treat it like code.

---

## Ingredient 3: The Frozen Snapshot

**Definition:** Read the memory file **once** at session start, inject it into the system prompt, and **never rebuild that prompt for the rest of the session** — even after the model writes new memories.

**Intuition.** Here's where most people make their first mistake. They think: "The model just wrote a memory, I should rebuild the prompt so it sees the new one." This is wrong, and it costs you a lot of money.

LLM providers cache identical prompt prefixes. Anthropic's prompt cache gives you roughly a 10× discount on tokens the model has already seen. If you mutate your system prompt mid-session, every turn's cache is a miss. You pay full price.

Instead: freeze the snapshot. Writes go to disk (durable across sessions), but the prompt stays stable (cache stays warm). The model sees the new memory **next session**, when you re-read the file.

**Minimum code:**

```python
class SessionContext:
    def __init__(self, memory: MemoryStore):
        self._snapshot = memory.read()  # frozen for this session

    def system_prompt_block(self) -> str:
        if not self._snapshot.strip():
            return ""
        return (
            "<player-memory>\n"
            "What we know about this player from prior sessions:\n\n"
            f"{self._snapshot}\n"
            "</player-memory>"
        )
```

**Why it works.** You get the best of both worlds — durability (writes land on disk immediately) and cheapness (prompt prefix stays identical, cache hits stack up).

**Gotcha.** Don't think of this as an optimization you can add later. If you build the loop with mutable per-turn prompts, retrofitting caching is painful. Build it frozen from day one.

**Bonus gotcha — the fence.** Notice the `<player-memory>` tags. Without them, models routinely treat remembered context as if the user just said it. They'll respond to a memory like it was a message. The fence tells the model "this is background data, not new input." Always fence injected context.

---

## Ingredient 4: The Nudge

**Definition:** A turn counter that, every N turns, hints to the model that now would be a good time to reflect on what's worth remembering.

**Intuition.** Even with the tool and the guidance in the description, models forget to use `remember`. Not because they can't, but because they're focused on the current task. The nudge is like a friend who occasionally taps you on the shoulder and says "hey, anything from that last conversation worth writing down?"

**Minimum code:**

```python
class Nudger:
    def __init__(self, interval: int = 5):
        self.interval = interval
        self.turns_since = 0

    def next_turn_hint(self) -> str | None:
        self.turns_since += 1
        if self.turns_since >= self.interval:
            self.turns_since = 0
            return (
                "[Coach aside: a few turns have passed. "
                "If a durable pattern has emerged about how this player "
                "handles these moments, call remember now.]"
            )
        return None
```

You prepend the hint to the next user message (wrapped so the model can see it's not from the player).

**Why it works.** It's a cheap external metronome. The model does the thinking; you supply the prompting.

**Gotcha.** If the interval is too small, every turn gets a nudge and the model starts saving noise to satisfy the prompt. Start at 5–10. Read what gets written. Adjust.

---

## Optional fifth ingredient: Retrieval + cheap-model summarization

For when you have *many* past sessions, not just a growing memory file.

**Intuition.** You don't want to inject every past transcript. Instead: at scene start, search past sessions for relevant ones, summarize each with a cheap model (Haiku, Flash), inject a few short summaries.

**Why the cheap model matters.** Summarization is the hard part. Retrieval (keyword search, FTS5, even plain `grep`) is easy. The trick is using a small fast model to compress a big transcript into a few useful sentences, then feeding those to your main model.

You probably don't need this for RealTalk v1. Flag it in your head for when players ask "does it remember the last game?"

---

## Putting it together: one turn

```
User message arrives
  ↓
Prepend nudge (if counter triggered)
  ↓
Send to LLM with frozen system prompt (snapshot included)
  ↓
Model may or may not call `remember`
  ↓
If it does, memory file is appended (durable NOW)
  ↓
Turn ends. Snapshot unchanged. Cache still warm.
```

And across sessions:

```
Session N ends. File has 12 observations.
  ↓
Session N+1 starts. Snapshot loaded fresh. 12 observations injected.
  ↓
The model "remembers." The player feels seen.
```

That's the whole mechanism. In Lesson 3 we build it in RealTalk.
