# 🤖 Innovation — AI Setup Agent

An AI-powered software installation agent that understands natural language and installs software automatically on your machine — using package managers where possible, and GUI automation as a fallback.

```bash
python main.py "install vlc and discord"
python main.py "Set up PostgreSQL and pgAdmin"
python main.py --force "install nodejs"
```

---

## How It Works

```
User Request
    ↓
main.py  →  LLM plans tasks  →  [ "install VLC", "install Discord" ]
    ↓
For each app:
  1. Installer.py  →  winget / apt / brew  (fast path)
  2. agent_graph.py  →  LangGraph state machine  (GUI fallback)
       ├── screen_reader.py   →  screenshot + OCR
       ├── app_identifier.py  →  identify what's on screen
       ├── doc_fetcher.py     →  fetch official install docs
       └── action_executor.py →  mouse/keyboard automation
```

---

## LLM Architecture

| Task | Provider |
|------|----------|
| Task planning | **Groq** (llama-3.3-70b) → Ollama fallback |
| Doc step extraction | **Groq** (llama-3.3-70b) → Ollama fallback |
| Failure advice | **Groq** (llama-3.1-8b-instant) → Ollama fallback |
| Screen reading (vision) | **Ollama llava** (always local) |

If `GROQ_API_KEY` is not set, everything runs on local Ollama automatically.

---

## Setup

### 1. Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com/download) running locally (for vision)
- [Groq API key](https://console.groq.com/keys) (free, for fast text inference)
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (for screen reading)

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Pull Ollama models

```bash
ollama pull llava     # vision model (required)
ollama pull llama3    # text fallback (used if Groq is unavailable)
```

### 4. Configure environment

```bash
cp .env.example .env
# Edit .env and add your GROQ_API_KEY
```

Or just set it inline:
```bash
export GROQ_API_KEY=gsk_...
```

### 5. Run

```bash
# Single app
python main.py "install vlc"

# Multiple apps (planned and executed sequentially)
python main.py "install discord and spotify"

# Force fresh start (ignore resume prompt)
python main.py --force "install postgresql and pgadmin"
```

---

## Features

- **Natural language input** — just describe what you want installed
- **Cross-platform** — Windows (winget), Linux (apt), macOS (brew)
- **Resume on crash** — progress is saved to disk, resume where you left off
- **GUI automation fallback** — handles apps not available in package managers
- **LLM-powered advice** — when a step fails, AI suggests a fix
- **Groq + Ollama** — fast cloud inference with local fallback, zero vendor lock-in

---

## Project Structure

```
Innovation/
├── main.py            # Entry point & orchestrator
├── llm_client.py      # Unified LLM client (Groq → Ollama fallback)
├── agent_graph.py     # LangGraph state machine for GUI installs
├── action_executor.py # Mouse/keyboard automation
├── screen_reader.py   # Screenshot + OCR
├── app_identifier.py  # Vision-based app identification
├── doc_fetcher.py     # Fetch & parse install documentation
├── Installer.py       # Package manager wrapper (winget/apt/brew)
├── config.py          # Centralized configuration
├── logger.py          # Logging setup
├── requirements.txt   # Python dependencies
└── .env.example       # Environment variable template
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Recommended | Groq API key for fast cloud LLM inference |
| `OLLAMA_HOST` | No | Ollama server URL (default: `http://localhost:11434`) |
| `AI_AGENT_DEBUG` | No | Set to `true` for verbose logging |

---

## License

MIT
