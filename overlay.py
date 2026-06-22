"""
overlay.py — Floating Overlay UI for the AI Agent
==================================================

A floating, always-on-top, semi-transparent widget using Tkinter.
Press Ctrl+Space (configurable) to show/hide the overlay.

Features:
    - Draggable, borderless window with dark Catppuccin-inspired styling
    - Scrollable, timestamped activity log (replaces single-line status)
    - Command input history: cycle with Up/Down arrow keys
    - Cooperative agent abort via threading.Event
    - Color-coded status indicator (idle / running / error / done)
    - Resizable window (drag bottom-right corner)
    - Global hotkey via the `keyboard` module

Dependencies:
    pip install keyboard

Usage:
    python overlay.py
    (Then press Ctrl+Space to toggle the overlay)
"""

import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
from datetime import datetime
import time

try:
    import keyboard
except ImportError:
    raise ImportError(
        "The 'keyboard' package is required. Install it with: pip install keyboard"
    )

try:
    import agent_graph
except ImportError:
    agent_graph = None
    print("[WARN] agent_graph.py not found. Agent integration will be mocked.")


# ===========================================================================
# Configuration
# ===========================================================================

HOTKEY = "ctrl+space"
OVERLAY_WIDTH = 440
OVERLAY_HEIGHT = 300
MIN_WIDTH = 320
MIN_HEIGHT = 200

# Catppuccin Mocha palette
BG_COLOR      = "#1e1e2e"
SURFACE_COLOR = "#313244"
FG_COLOR      = "#cdd6f4"
SUBTEXT_COLOR = "#6c7086"
ACCENT_COLOR  = "#89b4fa"   # Blue
GREEN_COLOR   = "#a6e3a1"   # Green (success)
YELLOW_COLOR  = "#f9e2af"   # Yellow (running)
RED_COLOR     = "#f38ba8"   # Red (error / abort)
BORDER_COLOR  = "#45475a"


# ===========================================================================
# Status States
# ===========================================================================

STATUS_IDLE    = ("idle",    "● Idle",    SUBTEXT_COLOR)
STATUS_RUNNING = ("running", "● Running", YELLOW_COLOR)
STATUS_DONE    = ("done",    "● Done",    GREEN_COLOR)
STATUS_ERROR   = ("error",   "● Error",   RED_COLOR)
STATUS_ABORTED = ("aborted", "● Aborted", RED_COLOR)


# ===========================================================================
# Overlay Window
# ===========================================================================

class OverlayWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AI Agent Overlay")
        self.root.geometry(f"{OVERLAY_WIDTH}x{OVERLAY_HEIGHT}")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.93)
        self.root.configure(bg=BORDER_COLOR)  # 1px border effect via padding

        # Center on screen
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - OVERLAY_WIDTH) // 2
        y = (sh - OVERLAY_HEIGHT) // 2
        self.root.geometry(f"+{x}+{y}")

        # State
        self.is_visible   = True
        self.agent_thread = None
        self.abort_event  = threading.Event()
        self._history: list[str] = []
        self._history_idx = -1
        self._drag_x = self._drag_y = 0
        self._resize_start = None

        self._build_ui()
        self._log("Overlay ready. Enter a command to start.", tag="system")

        keyboard.add_hotkey(HOTKEY, self._safe_toggle)

    # -----------------------------------------------------------------------
    # UI Construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        mono  = tkfont.Font(family="Consolas",  size=9)
        ui    = tkfont.Font(family="Segoe UI",  size=10)
        title = tkfont.Font(family="Segoe UI",  size=11, weight="bold")

        # Outer frame (sits on BG_COLOR border)
        outer = tk.Frame(self.root, bg=BG_COLOR)
        outer.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # ── Top bar ─────────────────────────────────────────────────────────
        bar = tk.Frame(outer, bg=BG_COLOR)
        bar.pack(fill=tk.X, padx=10, pady=(8, 0))

        tk.Label(bar, text="🤖 Agent", font=title, bg=BG_COLOR,
                 fg=ACCENT_COLOR).pack(side=tk.LEFT)

        # Status indicator (right side of title bar)
        self._status_var = tk.StringVar(value=STATUS_IDLE[1])
        self._status_lbl = tk.Label(bar, textvariable=self._status_var,
                                    font=ui, bg=BG_COLOR, fg=SUBTEXT_COLOR)
        self._status_lbl.pack(side=tk.LEFT, padx=(8, 0))

        # Close button
        tk.Button(bar, text="✕", bg=BG_COLOR, fg=SUBTEXT_COLOR,
                  activebackground=RED_COLOR, activeforeground=BG_COLOR,
                  borderwidth=0, relief=tk.FLAT, cursor="hand2",
                  command=self.hide).pack(side=tk.RIGHT)

        # Make entire bar draggable
        for widget in (bar, *bar.winfo_children()):
            widget.bind("<Button-1>",   self._drag_start)
            widget.bind("<B1-Motion>",  self._drag_move)

        # ── Log area ────────────────────────────────────────────────────────
        log_frame = tk.Frame(outer, bg=BG_COLOR)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 0))

        self._log_text = tk.Text(
            log_frame, font=mono, bg=SURFACE_COLOR, fg=FG_COLOR,
            insertbackground=FG_COLOR, relief=tk.FLAT,
            wrap=tk.WORD, state=tk.DISABLED,
            selectbackground=BORDER_COLOR,
        )
        scrollbar = tk.Scrollbar(log_frame, orient=tk.VERTICAL,
                                  command=self._log_text.yview,
                                  bg=SURFACE_COLOR, troughcolor=BG_COLOR,
                                  activebackground=BORDER_COLOR,
                                  relief=tk.FLAT, width=8)
        self._log_text.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tag styles for different log levels
        self._log_text.tag_config("system",  foreground=SUBTEXT_COLOR)
        self._log_text.tag_config("info",    foreground=FG_COLOR)
        self._log_text.tag_config("success", foreground=GREEN_COLOR)
        self._log_text.tag_config("error",   foreground=RED_COLOR)
        self._log_text.tag_config("ts",      foreground=BORDER_COLOR)

        # ── Input row ───────────────────────────────────────────────────────
        input_row = tk.Frame(outer, bg=BG_COLOR)
        input_row.pack(fill=tk.X, padx=10, pady=(6, 6))

        self._entry_var = tk.StringVar()
        self._entry = tk.Entry(
            input_row, textvariable=self._entry_var,
            font=ui, bg=SURFACE_COLOR, fg=FG_COLOR,
            insertbackground=FG_COLOR, relief=tk.FLAT,
            highlightthickness=1, highlightcolor=ACCENT_COLOR,
            highlightbackground=BORDER_COLOR,
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5,
                         padx=(0, 6))
        self._entry.bind("<Return>",   lambda _: self._start_agent())
        self._entry.bind("<Up>",       self._history_prev)
        self._entry.bind("<Down>",     self._history_next)
        self._entry.focus_set()

        btn_cfg = dict(font=ui, relief=tk.FLAT, cursor="hand2",
                       padx=10, pady=4)

        self._run_btn = tk.Button(
            input_row, text="Run", bg=ACCENT_COLOR, fg=BG_COLOR,
            activebackground=FG_COLOR, activeforeground=BG_COLOR,
            command=self._start_agent, **btn_cfg)
        self._run_btn.pack(side=tk.LEFT, padx=(0, 4))

        self._abort_btn = tk.Button(
            input_row, text="Abort", bg=BORDER_COLOR, fg=SUBTEXT_COLOR,
            activebackground=RED_COLOR, activeforeground=BG_COLOR,
            command=self._abort_agent, state=tk.DISABLED, **btn_cfg)
        self._abort_btn.pack(side=tk.LEFT)

        # ── Resize grip (bottom-right corner) ───────────────────────────────
        grip = tk.Label(outer, text="⠿", bg=BG_COLOR, fg=BORDER_COLOR,
                        cursor="size_nw_se")
        grip.pack(side=tk.RIGHT, anchor="se")
        grip.bind("<Button-1>",  self._resize_start)
        grip.bind("<B1-Motion>", self._resize_move)

    # -----------------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------------

    def _log(self, message: str, tag: str = "info"):
        """Append a timestamped line to the log widget (thread-safe)."""
        def _append():
            ts = datetime.now().strftime("%H:%M:%S")
            self._log_text.configure(state=tk.NORMAL)
            self._log_text.insert(tk.END, f"{ts}  ", "ts")
            self._log_text.insert(tk.END, f"{message}\n", tag)
            self._log_text.configure(state=tk.DISABLED)
            self._log_text.see(tk.END)
        self.root.after(0, _append)

    def _set_status(self, state_tuple):
        _, label, color = state_tuple
        self.root.after(0, lambda: (
            self._status_var.set(label),
            self._status_lbl.configure(fg=color),
        ))

    # -----------------------------------------------------------------------
    # Command History
    # -----------------------------------------------------------------------

    def _history_prev(self, _event):
        if not self._history:
            return
        self._history_idx = max(0, self._history_idx - 1)
        self._entry_var.set(self._history[self._history_idx])
        self._entry.icursor(tk.END)

    def _history_next(self, _event):
        if not self._history:
            return
        if self._history_idx < len(self._history) - 1:
            self._history_idx += 1
            self._entry_var.set(self._history[self._history_idx])
        else:
            self._history_idx = len(self._history)
            self._entry_var.set("")
        self._entry.icursor(tk.END)

    # -----------------------------------------------------------------------
    # Visibility
    # -----------------------------------------------------------------------

    def _safe_toggle(self):
        """Called from keyboard hotkey thread — must post to Tk main loop."""
        self.root.after(0, self.toggle_visibility)

    def toggle_visibility(self):
        self.hide() if self.is_visible else self.show()

    def hide(self):
        self.root.withdraw()
        self.is_visible = False

    def show(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self._entry.focus_set()
        self.is_visible = True

    # -----------------------------------------------------------------------
    # Dragging
    # -----------------------------------------------------------------------

    def _drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _drag_move(self, event):
        x = self.root.winfo_x() - self._drag_x + event.x
        y = self.root.winfo_y() - self._drag_y + event.y
        self.root.geometry(f"+{x}+{y}")

    # -----------------------------------------------------------------------
    # Resizing
    # -----------------------------------------------------------------------

    def _resize_start(self, event):
        self._resize_start = (event.x_root, event.y_root,
                              self.root.winfo_width(),
                              self.root.winfo_height())

    def _resize_move(self, event):
        if not self._resize_start:
            return
        ox, oy, ow, oh = self._resize_start
        nw = max(MIN_WIDTH,  ow + event.x_root - ox)
        nh = max(MIN_HEIGHT, oh + event.y_root - oy)
        self.root.geometry(f"{nw}x{nh}")

    # -----------------------------------------------------------------------
    # Agent Execution
    # -----------------------------------------------------------------------

    def _start_agent(self):
        if self._is_running():
            return

        command = self._entry_var.get().strip()
        if not command:
            self._log("Please enter a command.", tag="error")
            return

        # Save to history (avoid consecutive duplicates)
        if not self._history or self._history[-1] != command:
            self._history.append(command)
        self._history_idx = len(self._history)

        self.abort_event.clear()
        self._run_btn.configure(state=tk.DISABLED)
        self._abort_btn.configure(state=tk.NORMAL,
                                   bg=RED_COLOR, fg=BG_COLOR)
        self._set_status(STATUS_RUNNING)
        self._log(f"→ {command}", tag="info")

        self.agent_thread = threading.Thread(
            target=self._agent_thread_body,
            args=(command,),
            daemon=True,
        )
        self.agent_thread.start()

    def _abort_agent(self):
        if not self._is_running():
            return
        self.abort_event.set()
        self._log("Abort requested…", tag="error")
        self._set_status(STATUS_ABORTED)
        self._reset_ui_state()

    def _is_running(self) -> bool:
        return (self.agent_thread is not None
                and self.agent_thread.is_alive())

    def _reset_ui_state(self):
        def _reset():
            self._run_btn.configure(state=tk.NORMAL)
            self._abort_btn.configure(state=tk.DISABLED,
                                       bg=BORDER_COLOR, fg=SUBTEXT_COLOR)
        self.root.after(0, _reset)

    def _agent_thread_body(self, command: str):
        """Background worker. Redirects stdout to the log panel."""

        class _PipeToLog:
            def __init__(self, log_fn):
                self._log = log_fn
                self._buf = ""

            def write(self, text: str):
                self._buf += text
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    line = line.strip()
                    if line and not line.startswith("="):
                        self._log(line)

            def flush(self):
                if self._buf.strip():
                    self._log(self._buf.strip())
                    self._buf = ""

        original_stdout = sys.stdout
        sys.stdout = _PipeToLog(self._log)

        try:
            if agent_graph:
                agent_graph.run_setup_agent(
                    app_name=command,
                    abort_event=self.abort_event,   # pass if your graph supports it
                )
            else:
                # ── Mock behaviour ──────────────────────────────────────────
                steps = [
                    "Resolving dependencies…",
                    "Downloading packages…",
                    "Running installer…",
                    "Applying configuration…",
                    "Verifying installation…",
                ]
                for i, step in enumerate(steps, 1):
                    if self.abort_event.is_set():
                        break
                    print(f"[{i}/{len(steps)}] {step}")
                    time.sleep(1.2)

            if self.abort_event.is_set():
                self._log("Agent stopped by user.", tag="error")
                self._set_status(STATUS_ABORTED)
            else:
                self._log("✓ Done.", tag="success")
                self._set_status(STATUS_DONE)

        except Exception as exc:
            self._log(f"Error: {exc}", tag="error")
            self._set_status(STATUS_ERROR)

        finally:
            sys.stdout = original_stdout
            self._reset_ui_state()


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    print(f"Starting overlay. Press {HOTKEY.upper()} to toggle.")
    app = OverlayWindow()
    app.root.mainloop()