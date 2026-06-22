# AI Setup Agent

A fully autonomous AI Setup Agent that can identify on-screen applications, fetch official installation documentation, and use computer vision and keyboard/mouse automation to install and configure software for you.

## Features

* **Multi-App Orchestration**: Parses natural language requests (e.g., "Install Docker and PostgreSQL") and creates sequential execution plans using LangGraph.
* **On-Screen Identification**: Uses OCR (Tesseract) and local Vision LLMs (Ollama + LLaVA) to identify what application is currently on screen.
* **Intelligent Doc Fetching**: Scrapes official documentation pages, recursively follows links, and extracts specific, actionable setup steps using local LLMs.
* **Robust Execution**: Plans UI interactions (clicks, typing, shortcuts), attempts keyboard fallbacks, and verifies success using computer vision screenshots.
* **Floating Overlay UI**: A Catppuccin-styled Tkinter overlay that provides real-time logs, status indicators, and an easy input bar for triggering the agent.
* **Privacy-First**: Designed to run entirely on local, open-weights models (via Ollama) keeping your screen captures completely private.

## Architecture

The project is built around a modular, object-oriented architecture:

* **`main.py`**: The `SetupOrchestrator` that parses user requests and coordinates the setup of multiple applications.
* **`agent_graph.py`**: The `SetupAgentGraph` which uses `langgraph` to build a reliable state machine with retries, failure handling, and user-in-the-loop interventions.
* **`action_executor.py`**: The `ActionExecutor` that converts setup steps into precise UI interactions (clicks, typing) via `pyautogui` and validates them using screenshots.
* **`app_identifier.py`**: Identifies applications from screenshots using Multimodal LLMs.
* **`doc_fetcher.py`**: Scrapes and parses setup instructions into actionable JSON steps.
* **`screen_reader.py`**: Handles screenshot capturing (`mss`) and text extraction (`pytesseract`).
* **`overlay.py`**: The floating Tkinter UI.
* **`config.py` & `logger.py`**: Centralized configuration and logging.

## Prerequisites

1. **Python 3.10+**
2. **Ollama**: Must be installed and running locally. [Download Ollama](https://ollama.com/download)
   * Pull the required models:
     ```bash
     ollama pull llava
     ollama pull llama3
     ```
3. **Tesseract OCR**: 
   * Windows: [Download Installer](https://github.com/UB-Mannheim/tesseract/wiki) (Install to default `C:\Program Files\Tesseract-OCR\tesseract.exe` or update `config.py`)

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/kunalroy23042009/Innovation.git
   cd Innovation
   ```
2. Install the required Python packages:
   ```bash
   pip install requests beautifulsoup4 ollama pypdf mss Pillow pytesseract pyautogui pygetwindow langgraph keyboard
   ```

## Usage

### Run via Command Line

You can run the orchestrator directly from the terminal by providing a natural language request:

```bash
python main.py "Install Docker Desktop and then install PostgreSQL"
```

To run a single app setup pipeline without the orchestrator:

```bash
python agent_graph.py --app "PostgreSQL"
```

### Run via the Floating Overlay

To use the interactive floating UI:

```bash
python overlay.py
```
* **Toggle UI**: Press `Ctrl+Space`
* Enter your setup command directly into the input bar and click **Run**.

## Configuration

All global settings (model selection, API keys, timeouts, color themes) are managed in `config.py`.

* Change `vision_model` (default: `llava`) and `text_model` (default: `llama3`).
* To use Groq for ultra-fast failure analysis, set the `GROQ_API_KEY` environment variable.

## Safety & Fail-Safes

* **PyAutoGUI Failsafe**: Move your mouse to any of the four corners of your screen (e.g., Top-Left `(0,0)`) to immediately abort all automation.
* **Dry Run Mode**: You can test the planning and documentation scraping without executing any actions using the `--dry-run` flag.

## License

MIT License
