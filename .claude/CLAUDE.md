# CLAUDE.md

Project guidance for Claude Code working on realtalk.

## Essential Commands

```bash
realtalk                             # Run the game
pytest                               # Run all tests
mypy realtalk/ && ruff check realtalk/ tests/  # Type check + lint
```

## Architecture: Layered Design

7 independent layers; each depends only on the layer below.

| Layer | Module | Responsibility |
|-------|--------|-----------------|
| 0 | `session.py` | Event-sourced game state (immutable events) |
| 1 | `storage.py` | JSONL persistence + rotation/archival |
| 2 | `config.py` | Layered config (3 tiers + CLI flags) |
| 3 | `api.py` | Multi-provider LLM via litellm.ai |
| 4 | `conversation.py` | Turn loop: input → LLM → tools → state |
| 5 | `hooks.py` | Pre/post tool hooks; `tools.py`, `game_tools.py` |
| – | `prompt.py`, `compact.py`, `permissions.py` | Supporting utilities |
| 6 | `cli.py` | Entry point (`realtalk` command) |

**Key pattern**: All state mutations in Layer 0 are events. Never mutate directly; call `session.add_event()`.

**Config tiers** (highest to lowest priority): CLI flags → `.realtalk/settings.local.json` → `.realtalk.json` → `~/.realtalk/config.json`

## Status & Specs

**Last pushed to GitHub**: Layer 5 (hook runner + tool system)

**Next work**: `docs/spec/v1.6.md` and `docs/spec/v2.0.md` define the path forward.

Build specs live in `docs/spec/v{N}.md`. Each version builds on the previous one — follow the **current version to completion** before moving to the next.

- **v1.0–v1.5**: Completed and shipped
- **v1.6**: Foundation refinements (pending)
- **v2.0**: TUI/gameplay (pending)
- **PRD**: `docs/prd/1.0.md` — overall project goals (refer here when creating or updating version specs)

**When implementing from a version doc:**
1. Read the PRD (`docs/prd/1.0.md`) to understand product intent
2. Open the current version spec (`docs/spec/v{N}.md`) — it contains the implementation checklist, acceptance criteria, and layer-by-layer tasks
3. Work through the spec to completion
4. Only move to the next version after current is done (all checkboxes marked, deliverables completed, lint and tests pass)

## Token Efficiency

- **Skip `test_api_integration.py`** — real API calls, run only with `PYTEST_INTEGRATION=1` if explicitly asked
- **Layer boundaries are hard** — Layer 0 has no Layer 1+ imports; Layer 3 has no Layer 4+ imports; changes in one layer usually don't touch others
- **Strictly follow**: .claudeignore
- **Task to files mapping**:
  - Game event → `session.py` + `test_session_*.py` only
  - LLM behavior → `api.py` + `test_api_litellm.py` only
  - Tool execution → `conversation.py`, `hooks.py`, `tools.py`
  - Config → `config.py` only (self-contained)
- Only touch files relevant to the current task
- Delete over add — minimal changes preferred
- Fix root causes, not symptoms

## Skill routing
When the user's request matches an available skill, ALWAYS invoke it using the Skill tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.

- Product ideas, "is this worth building" → /office-hours
- Bugs, errors, "why is this broken" → /investigate
- Ship, deploy, push, create PR → /ship
- QA, test the app, find bugs → /qa
- Code review, check my diff → /review
- Update docs after shipping → /document-release
- Weekly retro → /retro
- Design system, branding → /design-consultation
- Visual audit, design polish → /design-review
- Architecture review → /plan-eng-review

Use /browse for all web browsing. Never use mcp__claude-in-chrome__* tools.

## Design System
Always read DESIGN.md before making any visual or UI decisions.
All colors, typography, spacing, status bar copy, and aesthetic direction are defined there.
Do not deviate without explicit user approval.
In QA mode, flag any TUI code that doesn't match DESIGN.md.
Key constraints: parchment light mode (#f5f0e8 bg), JetBrains Mono, single shifting accent (#c4703a warm / #c4a03a mid / #5b8fa8 cold), block character status bars (█▓▒░), emotional copy labels (Warm/Strained/Fragile), ASCII scene headers, borderless dialogue zone.
