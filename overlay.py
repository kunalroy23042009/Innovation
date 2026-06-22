"""
overlay.py — Floating Overlay UI for the AI Agent
==================================================

A floating, always-on-top, semi-transparent widget using Tkinter.
Refactored to align with the rest of the object-oriented architecture.
"""

import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
from datetime import datetime
import time

from config import config
from logger import logger, log_step

try:
    import keyboard
except ImportError:
    raise ImportError("The 'keyboard' package is required. Install it with: pip install keyboard")

try:
    from agent_graph import run_setup_agent
    HAS_AGENT = True
except ImportError:
    HAS_AGENT = False
    logger.warning("agent_graph.py not found. Agent integration will be mocked.")


class OverlayConfig:
    HOTKEY = "ctrl+space"
    OVERLAY_WIDTH = 440
    OVERLAY_HEIGHT = 300
    MIN_WIDTH = 320
    MIN_HEIGHT = 200

    BG_COLOR      = "#1e1e2e"
    SURFACE_COLOR = "#313244"
    FG_COLOR      = "#cdd6f4"
    SUBTEXT_COLOR = "#6c7086"
    ACCENT_COLOR  = "#89b4fa"
    GREEN_COLOR   = "#a6e3a1"
    YELLOW_COLOR  = "#f9e2af"
    RED_COLOR     = "#f38ba8"
    BORDER_COLOR  = "#45475a"

    STATUS_IDLE    = ("idle",    "● Idle",    SUBTEXT_COLOR)
    STATUS_RUNNING = ("running", "● Running", YELLOW_COLOR)
    STATUS_DONE    = ("done",    "● Done",    GREEN_COLOR)
    STATUS_ERROR   = ("error",   "● Error",   RED_COLOR)
    STATUS_ABORTED = ("aborted", "● Aborted", RED_COLOR)


class OverlayWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AI Agent Overlay")
        self.root.geometry(f"{OverlayConfig.OVERLAY_WIDTH}x{OverlayConfig.OVERLAY_HEIGHT}")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.93)
        self.root.configure(bg=OverlayConfig.BORDER_COLOR)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - OverlayConfig.OVERLAY_WIDTH) // 2
        y = (sh - OverlayConfig.OVERLAY_HEIGHT) // 2
        self.root.geometry(f"+{x}+{y}")

        self.is_visible = True
        self.agent_thread = None
        self.abort_event = threading.Event()
        self._history = []
        self._history_idx = -1
        self._drag_x = self._drag_y = 0
        self._resize_start = None

        self._build_ui()
        self._log("Overlay ready. Enter a command to start.", tag="system")

        keyboard.add_hotkey(OverlayConfig.HOTKEY, self._safe_toggle)

    def _build_ui(self):
        mono = tkfont.Font(family="Consolas", size=9)
        ui = tkfont.Font(family="Segoe UI", size=10)
        title = tkfont.Font(family="Segoe UI", size=11, weight="bold")

        outer = tk.Frame(self.root, bg=OverlayConfig.BG_COLOR)
        outer.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        bar = tk.Frame(outer, bg=OverlayConfig.BG_COLOR)
        bar.pack(fill=tk.X, padx=10, pady=(8, 0))

        tk.Label(bar, text="🤖 Agent", font=title, bg=OverlayConfig.BG_COLOR, fg=OverlayConfig.ACCENT_COLOR).pack(side=tk.LEFT)

        self._status_var = tk.StringVar(value=OverlayConfig.STATUS_IDLE[1])
        self._status_lbl = tk.Label(bar, textvariable=self._status_var, font=ui, bg=OverlayConfig.BG_COLOR, fg=OverlayConfig.SUBTEXT_COLOR)
        self._status_lbl.pack(side=tk.LEFT, padx=(8, 0))

        tk.Button(
            bar, text="✕", bg=OverlayConfig.BG_COLOR, fg=OverlayConfig.SUBTEXT_COLOR,
            activebackground=OverlayConfig.RED_COLOR, activeforeground=OverlayConfig.BG_COLOR,
            borderwidth=0, relief=tk.FLAT, cursor="hand2", command=self.hide
        ).pack(side=tk.RIGHT)

        for widget in (bar, *bar.winfo_children()):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        log_frame = tk.Frame(outer, bg=OverlayConfig.BG_COLOR)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 0))

        self._log_text = tk.Text(
            log_frame, font=mono, bg=OverlayConfig.SURFACE_COLOR, fg=OverlayConfig.FG_COLOR,
            insertbackground=OverlayConfig.FG_COLOR, relief=tk.FLAT, wrap=tk.WORD, state=tk.DISABLED,
            selectbackground=OverlayConfig.BORDER_COLOR,
        )
        scrollbar = tk.Scrollbar(
            log_frame, orient=tk.VERTICAL, command=self._log_text.yview,
            bg=OverlayConfig.SURFACE_COLOR, troughcolor=OverlayConfig.BG_COLOR,
            activebackground=OverlayConfig.BORDER_COLOR, relief=tk.FLAT, width=8
        )
        self._log_text.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._log_text.tag_config("system",  foreground=OverlayConfig.SUBTEXT_COLOR)
        self._log_text.tag_config("info",    foreground=OverlayConfig.FG_COLOR)
        self._log_text.tag_config("success", foreground=OverlayConfig.GREEN_COLOR)
        self._log_text.tag_config("error",   foreground=OverlayConfig.RED_COLOR)
        self._log_text.tag_config("ts",      foreground=OverlayConfig.BORDER_COLOR)

        input_row = tk.Frame(outer, bg=OverlayConfig.BG_COLOR)
        input_row.pack(fill=tk.X, padx=10, pady=(6, 6))

        self._entry_var = tk.StringVar()
        self._entry = tk.Entry(
            input_row, textvariable=self._entry_var, font=ui, bg=OverlayConfig.SURFACE_COLOR, fg=OverlayConfig.FG_COLOR,
            insertbackground=OverlayConfig.FG_COLOR, relief=tk.FLAT, highlightthickness=1,
            highlightcolor=OverlayConfig.ACCENT_COLOR, highlightbackground=OverlayConfig.BORDER_COLOR,
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5, padx=(0, 6))
        self._entry.bind("<Return>", lambda _: self._start_agent())
        self._entry.bind("<Up>", self._history_prev)
        self._entry.bind("<Down>", self._history_next)
        self._entry.focus_set()

        btn_cfg = dict(font=ui, relief=tk.FLAT, cursor="hand2", padx=10, pady=4)

        self._run_btn = tk.Button(
            input_row, text="Run", bg=OverlayConfig.ACCENT_COLOR, fg=OverlayConfig.BG_COLOR,
            activebackground=OverlayConfig.FG_COLOR, activeforeground=OverlayConfig.BG_COLOR,
            command=self._start_agent, **btn_cfg
        )
        self._run_btn.pack(side=tk.LEFT, padx=(0, 4))

        self._abort_btn = tk.Button(
            input_row, text="Abort", bg=OverlayConfig.BORDER_COLOR, fg=OverlayConfig.SUBTEXT_COLOR,
            activebackground=OverlayConfig.RED_COLOR, activeforeground=OverlayConfig.BG_COLOR,
            command=self._abort_agent, state=tk.DISABLED, **btn_cfg
        )
        self._abort_btn.pack(side=tk.LEFT)

        grip = tk.Label(outer, text="⠿", bg=OverlayConfig.BG_COLOR, fg=OverlayConfig.BORDER_COLOR, cursor="size_nw_se")
        grip.pack(side=tk.RIGHT, anchor="se")
        grip.bind("<Button-1>", self._resize_start)
        grip.bind("<B1-Motion>", self._resize_move)

    def _log(self, message: str, tag: str = "info"):
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

    def _history_prev(self, _event):
        if not self._history: return
        self._history_idx = max(0, self._history_idx - 1)
        self._entry_var.set(self._history[self._history_idx])
        self._entry.icursor(tk.END)

    def _history_next(self, _event):
        if not self._history: return
        if self._history_idx < len(self._history) - 1:
            self._history_idx += 1
            self._entry_var.set(self._history[self._history_idx])
        else:
            self._history_idx = len(self._history)
            self._entry_var.set("")
        self._entry.icursor(tk.END)

    def _safe_toggle(self):
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

    def _drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _drag_move(self, event):
        x = self.root.winfo_x() - self._drag_x + event.x
        y = self.root.winfo_y() - self._drag_y + event.y
        self.root.geometry(f"+{x}+{y}")

    def _resize_start(self, event):
        self._resize_start = (event.x_root, event.y_root, self.root.winfo_width(), self.root.winfo_height())

    def _resize_move(self, event):
        if not self._resize_start: return
        ox, oy, ow, oh = self._resize_start
        nw = max(OverlayConfig.MIN_WIDTH, ow + event.x_root - ox)
        nh = max(OverlayConfig.MIN_HEIGHT, oh + event.y_root - oy)
        self.root.geometry(f"{nw}x{nh}")

    def _start_agent(self):
        if self._is_running(): return

        command = self._entry_var.get().strip()
        if not command:
            self._log("Please enter a command.", tag="error")
            return

        if not self._history or self._history[-1] != command:
            self._history.append(command)
        self._history_idx = len(self._history)

        self.abort_event.clear()
        self._run_btn.configure(state=tk.DISABLED)
        self._abort_btn.configure(state=tk.NORMAL, bg=OverlayConfig.RED_COLOR, fg=OverlayConfig.BG_COLOR)
        self._set_status(OverlayConfig.STATUS_RUNNING)
        self._log(f"→ {command}", tag="info")

        self.agent_thread = threading.Thread(target=self._agent_thread_body, args=(command,), daemon=True)
        self.agent_thread.start()

    def _abort_agent(self):
        if not self._is_running(): return
        self.abort_event.set()
        self._log("Abort requested…", tag="error")
        self._set_status(OverlayConfig.STATUS_ABORTED)
        self._reset_ui_state()

    def _is_running(self) -> bool:
        return self.agent_thread is not None and self.agent_thread.is_alive()

    def _reset_ui_state(self):
        def _reset():
            self._run_btn.configure(state=tk.NORMAL)
            self._abort_btn.configure(state=tk.DISABLED, bg=OverlayConfig.BORDER_COLOR, fg=OverlayConfig.SUBTEXT_COLOR)
        self.root.after(0, _reset)

    def _agent_thread_body(self, command: str):
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
            if HAS_AGENT:
                run_setup_agent(app_name=command)
            else:
                steps = ["Resolving dependencies…", "Downloading packages…", "Running installer…", "Applying configuration…", "Verifying installation…"]
                for i, step in enumerate(steps, 1):
                    if self.abort_event.is_set(): break
                    print(f"[{i}/{len(steps)}] {step}")
                    time.sleep(1.2)

            if self.abort_event.is_set():
                self._log("Agent stopped by user.", tag="error")
                self._set_status(OverlayConfig.STATUS_ABORTED)
            else:
                self._log("✓ Done.", tag="success")
                self._set_status(OverlayConfig.STATUS_DONE)

        except Exception as exc:
            self._log(f"Error: {exc}", tag="error")
            self._set_status(OverlayConfig.STATUS_ERROR)

        finally:
            sys.stdout = original_stdout
            self._reset_ui_state()

if __name__ == "__main__":
    logger.info(f"Starting overlay. Press {OverlayConfig.HOTKEY.upper()} to toggle.")
    app = OverlayWindow()
    app.root.mainloop()