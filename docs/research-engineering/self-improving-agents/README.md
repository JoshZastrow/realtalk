# Self-Improving Agents: A Mini-Course

*A course in four lessons, inspired by a close reading of the `hermes-agent` learning loop and aimed at making your RealTalk LLM remember who you are.*

## What you'll learn

By the end you can answer "yes" to all of these:

- I can explain in one sentence how an agent can "remember" across sessions without fine-tuning.
- I can name the four small parts that make it work.
- I can wire those parts into RealTalk's turn loop and see the effect on the next playthrough.
- I know the three mistakes that will silently ruin the design.

## The lessons

1. **[The big idea](01-the-big-idea.md)** — the story of why most teams reach for the wrong tool first.
2. **[The four ingredients](02-the-four-ingredients.md)** — the conceptual model, with code for each part.
3. **[Cookbook](03-cookbook-build-it-in-realtalk.md)** — building a persistent `PlayerProfile` in RealTalk, step by step.
4. **[Pitfalls & production notes](04-pitfalls-and-production-notes.md)** — what to watch for once it works.

## Before you start

You should be comfortable with:

- Python classes and dataclasses
- How RealTalk's turn loop works (skim `realtalk/conversation.py:226` onward)
- The concept of a "system prompt" and "tool use"

That's it. No vector DBs, no fine-tuning, no framework required.

## A note on style

These lessons are written to be *read aloud*. Short paragraphs. Concrete code. One idea at a time. If the text ever feels abstract, skip ahead to the code — it's always simpler than it sounds.

## Where this came from

The patterns in this course are lifted almost verbatim from a close reading of `lib/hermes-agent/` — a production self-improving agent sitting in the repo. If you ever want to see the original, the key files are:

- `lib/hermes-agent/agent/memory_manager.py` — the store and prefetch layer
- `lib/hermes-agent/tools/memory_tool.py` — the tool the model calls
- `lib/hermes-agent/agent/prompt_builder.py` — the guidance prose
- `lib/hermes-agent/tools/session_search_tool.py` — the optional retrieval path

You don't need to read them to follow the course. They're there when you're curious.
