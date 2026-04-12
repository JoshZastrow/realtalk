# realtalk

A terminal-based conversational game. Navigate emotionally charged social interactions
with an LLM-powered character. Read the room. Make your move.

---

## Quickstart

**Requirements:** Python 3.11+, [`uv`](https://docs.astral.sh/uv/)

```bash
git clone https://github.com/JoshZastrow/realtalk.git
cd realtalk

# Create and activate the virtual environment
uv venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install the package in editable mode with dev dependencies
uv pip install -e ".[dev]"

# Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run the game
realtalk
```

---

## Play

```
realtalk                          # standard game
realtalk display.no_color=true    # no color output
realtalk display.debug=true       # show LLM prompt structure
realtalk contributor.enabled=true # opt-in to RLHF data collection
```

Config flags use dotted paths into the config hierarchy. See `docs/prd/0.1.md` for
full game rules and `docs/spec/` for technical design.

---

## Development

### Run tests

```bash
pytest
```

### Run with coverage

```bash
pytest --cov=realtalk --cov-report=term-missing
```

### Type check

```bash
mypy realtalk/
```

### Lint

```bash
ruff check realtalk/ tests/
```

---

## Project configuration

Game settings can be layered across three config files, lower tiers win:

| Tier | File | Purpose |
|------|------|---------|
| 1 (lowest) | `~/.realtalk/config.json` | Your personal defaults across all projects |
| 2 | `.realtalk.json` | Project settings — commit this |
| 3 (highest) | `.realtalk/settings.local.json` | Machine-specific overrides — gitignore this |

Example `.realtalk.json`:

```json
{
  "game": {
    "model": "claude-haiku-4-5-20251001",
    "arc_trigger_threshold": 80
  },
  "display": {
    "no_color": false
  }
}
```

CLI flags (e.g. `display.no_color=true`) override all file-based config.

---

## Contributor mode

To contribute gameplay data for model training (always opt-in, always anonymized):

```bash
realtalk contributor.enabled=true
```

Session data is stored locally at `~/.realtalk/sessions/`. Nothing is transmitted
in v0.1 — the data stays on your machine until an upload mechanism is added.

---

## Architecture

Built in layers — each layer depends only on the layer below it.

| Spec | Layer | File | Status |
|------|-------|------|--------|
| v0.1 | 0 | `realtalk/session.py` | Done — event-sourced session model + JSONL serialization (71 tests) |
| v0.2 | 1 | `realtalk/storage.py` | Skeleton — disk persistence + log rotation |
| v0.2 | 2 | `realtalk/config.py` | Skeleton — layered config via `chz` + pydantic |
| v0.3 | 3 | `realtalk/api.py` | Skeleton — streaming Anthropic client |
| v0.3 | 4 | `realtalk/conversation.py` | Planned — game loop engine |
| v0.4 | 5 | `realtalk/game.py` | Planned — scene, role, turn mechanics |
| v0.5 | 6 | `realtalk/tui.py` | Planned — terminal UI |
| v0.6 | 7 | `realtalk/cli.py` | Skeleton — entry point via `chz.nested_entrypoint` |

See `docs/spec/` for detailed specs and acceptance criteria per layer.

---

## Docs

- [`docs/prd/0.1.md`](docs/prd/0.1.md) — Product requirements document
- [`docs/spec/v0.1.md`](docs/spec/v0.1.md) — Build spec: session data types (Layer 0)
- [`docs/spec/v0.2.md`](docs/spec/v0.2.md) — Build spec: storage + configuration (Layers 1–2)
