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

## Run the Game

### Standard gameplay

```bash
realtalk
```

### With options

```bash
realtalk display.no_color=true       # no color output (accessibility)
realtalk display.debug=true          # show LLM prompt structure for debugging
realtalk contributor.enabled=true    # opt-in to RLHF data collection
realtalk game.model=gpt-4            # use OpenAI GPT-4 instead of Claude
realtalk game.model=ollama/llama2    # use local Ollama model
```

Config flags use dotted paths into the config hierarchy. See `docs/prd/1.0.md` for
full game rules and `docs/spec/` for technical design.

### Multi-provider support

v1.3 adds multi-provider LLM support via litellm.ai. You can use any model:

```bash
# Anthropic (default)
realtalk game.model=claude-3-5-sonnet-20241022

# OpenAI
export OPENAI_API_KEY=sk-...
realtalk game.model=gpt-4-turbo

# Google
export GOOGLE_APPLICATION_CREDENTIALS=path/to/credentials.json
realtalk game.model=gemini-2.0-flash

# Local (Ollama)
realtalk game.model=ollama/llama2
```

See [litellm provider docs](https://docs.litellm.ai/docs/providers) for full list.

---

## Development

### Run tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_session.py

# Run with verbose output
pytest -v

# To run including integration tests to make real API calls, add to .env:
PYTEST_INTEGRATION=1
LITELLM_DEBUG=1
# and un- SKIP tests in test_api_integration.py


# Run with coverage
pytest --cov=realtalk --cov-report=term-missing

# Run specific test
pytest tests/test_api_litellm.py::test_litellm_client_init
```

Tests cover:
- **Layer 0 (session.py):** Event-sourced session model — 72 tests (49 pytest + 23 doctests)
- **Layer 1 (storage.py):** Disk persistence, rotation, archival — 34 tests (16 pytest + 18 doctests)
- **Layer 2 (config.py):** Layered configuration — 21 tests
- **Layer 3 (api.py):** LLM provider integration — 28 tests
- **Layer 4 (conversation.py):** Conversation engine, tool loop, message formatting — 17 tests
- **Total:** 161 collected (155 passing, 6 skipped)

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

## Contributor Mode

To contribute gameplay data for model training (always opt-in, always anonymized):

```bash
realtalk contributor.enabled=true
```

Session data is stored locally at `~/.realtalk/sessions/`. Nothing is transmitted
in v1.0 — the data stays on your machine until an upload mechanism is added.

---

## LLM API Integration (Layer 3)

The game uses `LiteLLMClient` to stream responses from any LLM provider via litellm.ai.

### Running the API directly (Python)

```python
from realtalk.api import LiteLLMClient, ApiRequest

# Create client
client = LiteLLMClient(model="claude-3-5-sonnet-20241022")

# Build request
request = ApiRequest(
    system_prompt=["You are a helpful assistant."],
    messages=[{"role": "user", "content": "Hello!"}],
    tools=[],
    model="claude-3-5-sonnet-20241022"
)

# Stream response
for event in client.stream(request):
    print(event)
```

### Supported Providers

litellm.ai auto-detects the provider from the model name:

| Provider | Model Examples | Auth |
|----------|---|---|
| Anthropic | `claude-3-5-sonnet-20241022`, `claude-opus-4-6` | `ANTHROPIC_API_KEY` env var |
| OpenAI | `gpt-4`, `gpt-4-turbo`, `gpt-3.5-turbo` | `OPENAI_API_KEY` env var |
| Google | `gemini-2.0-flash`, `gemini-1.5-pro` | `GOOGLE_APPLICATION_CREDENTIALS` env var |
| Meta (Llama) | `meta-llama/llama-2-7b`, `meta-llama/llama-2-13b` | `REPLICATE_API_TOKEN` env var |
| Local (Ollama) | `ollama/llama2`, `ollama/mistral` | `OLLAMA_API_BASE` env var |

Full list: https://docs.litellm.ai/docs/providers

---

## Architecture

Built in layers — each layer depends only on the layer below it.

| Spec | Layer | File | Status |
|------|-------|------|--------|
| v1.0 | 0 | `realtalk/session.py` | ✓ Done — event-sourced model + JSONL serialization (71 tests) |
| v1.1 | 1 | `realtalk/storage.py` | ✓ Done — disk persistence, rotation, archival (16 tests) |
| v1.1 | 2 | `realtalk/config.py` | ✓ Done — layered config via `chz` + pydantic (14 tests) |
| v1.3 | 3 | `realtalk/api.py` | ✓ Done — multi-provider LLM via litellm.ai (23 tests) |
| v1.4 | 4 | `realtalk/conversation.py` | ✓ Done — conversation engine, tool loop, message formatting (17 tests) |
| v0.5 | 5 | `realtalk/game.py` | Planned — scene, role, turn mechanics |
| v0.6 | 6 | `realtalk/tui.py` | Planned — terminal UI |
| v0.7 | 7 | `realtalk/cli.py` | Skeleton — entry point via `chz.nested_entrypoint` |

**Current status:** Layers 0–4 complete (event sourcing, persistence, config, multi-provider LLM, conversation engine).

See `docs/spec/` for detailed specs and acceptance criteria per layer.

---

## Docs

- [`docs/prd/1.0.md`](docs/prd/1.0.md) — Product requirements document
- [`docs/spec/v1.0.md`](docs/spec/v1.0.md) — Build spec: session data types (Layer 0)
- [`docs/spec/v1.1.md`](docs/spec/v1.1.md) — Build spec: storage + configuration (Layers 1–2)
- [`docs/spec/v1.3.md`](docs/spec/v1.3.md) — Build spec: multi-provider LLM integration (Layer 3)
- [`docs/spec/v1.4.md`](docs/spec/v1.4.md) — Build spec: conversation engine (Layer 4)
- [`docs/design/v1.2-design.md`](docs/design/v1.2-design.md) — Engineering review findings
