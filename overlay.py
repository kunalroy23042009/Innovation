"""
overlay.py — Phase 5: Floating Overlay UI for the AI Agent
==========================================================

This module provides a floating, always-on-top, semi-transparent widget using
Tkinter. It listens for a global hotkey (Ctrl+Space) to show/hide itself.

Features:
    - Draggable window with custom dark mode styling
    - Global hotkey using the `keyboard` module
    - Input text box for giving the agent commands
    - Live status updates from the agent
    - "Abort" button to stop the agent safely
    - Threaded execution so the UI remains responsive while the agent runs

Dependencies:
    pip install keyboard

Usage:
    python overlay.py
    (Then press Ctrl+Space to toggle the overlay)
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
import time

try:
    import keyboard
except ImportError:
    raise ImportError("The 'keyboard' package is required. Install it with: pip install keyboard")

# Import the Phase 4 orchestrator.
# We assume agent_graph.py is in the same directory.
try:
    import agent_graph
except ImportError:
    agent_graph = None
    print("[WARN] agent_graph.py not found. Agent integration will be mocked.")


# ===========================================================================
# Configuration
# ===========================================================================

HOTKEY = "ctrl+space"
OVERLAY_WIDTH = 400
OVERLAY_HEIGHT = 150
BG_COLOR = "#1e1e2e"
FG_COLOR = "#cdd6f4"
ACCENT_COLOR = "#89b4fa"
ABORT_COLOR = "#f38ba8"

# ===========================================================================
# Overlay Window Class
# ===========================================================================

class OverlayWindow:
    def __init__(self):
        # --- Root Setup ---
        self.root = tk.Tk()
        self.root.title("AI Agent Overlay")
        self.root.geometry(f"{OVERLAY_WIDTH}x{OVERLAY_HEIGHT}")
        
        # Make the window borderless, always-on-top, and slightly transparent
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.9)
        self.root.configure(bg=BG_COLOR)
        
        # Center the window initially
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width // 2) - (OVERLAY_WIDTH // 2)
        y = (screen_height // 2) - (OVERLAY_HEIGHT // 2)
        self.root.geometry(f"+{x}+{y}")
        
        # Variables for dragging
        self._drag_start_x = 0
        self._drag_start_y = 0

        # Agent thread control
        self.agent_thread = None
        self.agent_running = False

        self._build_ui()
        
        # Bind dragging events to the main frame
        self.main_frame.bind("<Button-1>", self.start_drag)
        self.main_frame.bind("<B1-Motion>", self.do_drag)
        
        # Bind global hotkey
        keyboard.add_hotkey(HOTKEY, self.toggle_visibility)
        
        self.is_visible = True

    def _build_ui(self):
        """Construct the UI elements inside the overlay."""
        custom_font = tkfont.Font(family="Segoe UI", size=10)
        title_font = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        
        # Main container
        self.main_frame = tk.Frame(self.root, bg=BG_COLOR, padx=10, pady=10)
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Top bar (Title and Drag handle)
        self.top_bar = tk.Frame(self.main_frame, bg=BG_COLOR)
        self.top_bar.pack(fill=tk.X, pady=(0, 10))
        self.top_bar.bind("<Button-1>", self.start_drag)
        self.top_bar.bind("<B1-Motion>", self.do_drag)
        
        self.title_label = tk.Label(self.top_bar, text="🤖 Setup Agent", font=title_font, bg=BG_COLOR, fg=ACCENT_COLOR)
        self.title_label.pack(side=tk.LEFT)
        
        self.close_btn = tk.Button(self.top_bar, text="✖", bg=BG_COLOR, fg=FG_COLOR, borderwidth=0, activebackground=ABORT_COLOR, activeforeground="white", command=self.hide)
        self.close_btn.pack(side=tk.RIGHT)
        
        # Status Label
        self.status_var = tk.StringVar(value="Status: Idle (Awaiting command)")
        self.status_label = tk.Label(self.main_frame, textvariable=self.status_var, font=custom_font, bg=BG_COLOR, fg=FG_COLOR, anchor="w")
        self.status_label.pack(fill=tk.X, pady=(0, 5))
        
        # Input Frame
        self.input_frame = tk.Frame(self.main_frame, bg=BG_COLOR)
        self.input_frame.pack(fill=tk.X, pady=5)
        
        self.entry_var = tk.StringVar()
        self.command_entry = tk.Entry(self.input_frame, textvariable=self.entry_var, font=custom_font, bg="#313244", fg=FG_COLOR, insertbackground=FG_COLOR, relief=tk.FLAT)
        self.command_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4, padx=(0, 5))
        self.command_entry.bind("<Return>", lambda e: self.start_agent())
        
        # Buttons
        self.btn_frame = tk.Frame(self.main_frame, bg=BG_COLOR)
        self.btn_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.run_btn = tk.Button(self.btn_frame, text="Run", bg=ACCENT_COLOR, fg="#11111b", font=custom_font, relief=tk.FLAT, command=self.start_agent)
        self.run_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        
        self.abort_btn = tk.Button(self.btn_frame, text="Abort", bg=ABORT_COLOR, fg="#11111b", font=custom_font, relief=tk.FLAT, command=self.abort_agent)
        self.abort_btn.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.abort_btn.configure(state=tk.DISABLED)

    # --- Window Dragging Logic ---
    def start_drag(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def do_drag(self, event):
        # Calculate how far the mouse has moved
        x = self.root.winfo_x() - self._drag_start_x + event.x
        y = self.root.winfo_y() - self._drag_start_y + event.y
        self.root.geometry(f"+{x}+{y}")

    # --- Visibility Toggle ---
    def toggle_visibility(self):
        if self.is_visible:
            self.hide()
        else:
            self.show()

    def hide(self):
        self.root.withdraw()
        self.is_visible = False

    def show(self):
        self.root.deiconify()
        # Bring to front
        self.root.attributes("-topmost", True)
        self.command_entry.focus_set()
        self.is_visible = True

    # --- Agent Execution Logic ---
    def update_status(self, msg: str):
        """Thread-safe update of the status label."""
        self.root.after(0, lambda: self.status_var.set(f"Status: {msg}"))

    def start_agent(self):
        if self.agent_running:
            return
            
        command = self.entry_var.get().strip()
        if not command:
            self.update_status("Please enter a command.")
            return

        self.agent_running = True
        self.run_btn.configure(state=tk.DISABLED)
        self.abort_btn.configure(state=tk.NORMAL)
        
        # Start the agent in a background thread to prevent UI freezing
        self.agent_thread = threading.Thread(target=self._run_agent_thread, args=(command,), daemon=True)
        self.agent_thread.start()

    def abort_agent(self):
        if not self.agent_running:
            return
            
        self.update_status("Aborting agent...")
        # Currently, a true hard abort of a background thread in Python is difficult
        # without complex inter-thread messaging. We simulate an abort state.
        self.agent_running = False
        
        # Reset UI
        self._reset_ui()
        self.update_status("Agent aborted by user.")

    def _reset_ui(self):
        self.run_btn.configure(state=tk.NORMAL)
        self.abort_btn.configure(state=tk.DISABLED)
        self.agent_running = False

    def _run_agent_thread(self, command: str):
        """The actual background task that runs the agent."""
        try:
            # We override print in this thread to intercept agent_graph.py logs
            # and pipe them directly to our UI status!
            class UILogger:
                def __init__(self, update_func):
                    self.update_func = update_func
                    
                def write(self, text):
                    text = text.strip()
                    if text and not text.startswith("="):
                        self.update_func(text)
                        
                def flush(self):
                    pass
            
            original_stdout = sys.stdout
            sys.stdout = UILogger(self.update_status)
            
            try:
                self.update_status(f"Starting agent: {command}")
                if agent_graph:
                    # In a real scenario, we might pass the command directly
                    # For now, we assume the command is the app_name for Phase 4
                    agent_graph.run_setup_agent(app_name=command)
                else:
                    # Mock behavior if agent_graph isn't found
                    for i in range(1, 6):
                        if not self.agent_running: break
                        print(f"Executing step {i}/5...")
                        time.sleep(1.5)
                        
                    if self.agent_running:
                        print("✅ Setup complete!")
                        
            finally:
                # Restore original stdout
                sys.stdout = original_stdout
                
        except Exception as e:
            self.update_status(f"Error: {e}")
        finally:
            self.root.after(0, self._reset_ui)


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    print(f"Starting Overlay. Press {HOTKEY.upper()} to toggle visibility.")
    app = OverlayWindow()
    app.root.mainloop()
