# editor.py
import os
import re
import json
import time
import hashlib
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter import font as tkfont

# Optional: PDF export (reportlab)
try:
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import letter
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False


APP_NAME = "Simple Text Editor++"
DEFAULT_AUTOSAVE_SECONDS = 15
MAX_RECENT = 10


PY_KEYWORDS = set("""
False None True and as assert break class continue def del elif else except finally for from global if import in is
lambda nonlocal not or pass raise return try while with yield
""".split())


def safe_read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return Path(path).read_text(errors="ignore")


def safe_write_text(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


class MacroRecorder:
    """
    Simple macro recorder:
      - Records inserted characters, Return, Tab, BackSpace, Delete
    """
    def __init__(self):
        self.recording = False
        self.events = []  # ("ins","a") or ("bs",) or ("del",)

    def start(self):
        self.recording = True
        self.events = []

    def stop(self):
        self.recording = False

    def record_key(self, event: tk.Event):
        if not self.recording:
            return

        ks = event.keysym
        ch = event.char

        if ks in ("BackSpace", "Delete"):
            self.events.append(("bs" if ks == "BackSpace" else "del",))
            return
        if ks == "Return":
            self.events.append(("ins", "\n"))
            return
        if ks == "Tab":
            self.events.append(("ins", "\t"))
            return

        if ch and len(ch) == 1 and ord(ch) >= 32:
            self.events.append(("ins", ch))

    def play(self, text_widget: tk.Text):
        for ev in self.events:
            if ev[0] == "ins":
                text_widget.insert("insert", ev[1])
            elif ev[0] == "bs":
                try:
                    text_widget.delete("insert-1c", "insert")
                except Exception:
                    pass
            elif ev[0] == "del":
                try:
                    text_widget.delete("insert", "insert+1c")
                except Exception:
                    pass


class Document:
    def __init__(self, app, filepath=None, initial_text=""):
        self.app = app
        self.filepath = filepath  # str or None
        self.dirty = False
        self.last_saved_hash = sha1(initial_text)
        self.autosave_id = sha1(f"{time.time()}-{id(self)}")

        # Font
        self.font_family = "Menlo" if "Menlo" in tkfont.families() else "Courier"
        self.font_size = 12
        self.font = tkfont.Font(family=self.font_family, size=self.font_size)

        # UI frame for this tab
        self.frame = ttk.Frame(app.notebook)

        # Layout: line nums | text | minimap
        self.body = ttk.Frame(self.frame)
        self.body.pack(fill="both", expand=True)

        # Line number canvas
        self.ln = tk.Canvas(self.body, width=50, highlightthickness=0)
        self.ln.pack(side="left", fill="y")

        # Text + scrollbar container
        self.text_frame = ttk.Frame(self.body)
        self.text_frame.pack(side="left", fill="both", expand=True)

        self.vsb = ttk.Scrollbar(self.text_frame, orient="vertical")
        self.vsb.pack(side="right", fill="y")

        self.text = tk.Text(
            self.text_frame,
            wrap=tk.WORD,
            undo=True,
            autoseparators=True,
            maxundo=-1,
            yscrollcommand=self._on_textscroll,
            font=self.font,
            tabs=("1c",),
        )
        self.text.pack(side="left", fill="both", expand=True)
        self.vsb.config(command=self._on_scrollbar)

        # Minimap canvas
        self.minimap = tk.Canvas(self.body, width=110, highlightthickness=0)
        self.minimap.pack(side="right", fill="y")

        # Behaviors
        self.show_whitespace = False
        self.syntax_highlight = True

        # Tags
        self._setup_tags()

        # Insert initial
        if initial_text:
            self.text.insert("1.0", initial_text)
            self.text.edit_modified(False)

        # Bind events
        self.text.bind("<<Modified>>", self._on_modified)
        self.text.bind("<KeyRelease>", self._on_cursor_activity)
        self.text.bind("<ButtonRelease-1>", self._on_cursor_activity)
        self.text.bind("<MouseWheel>", self._on_cursor_activity)
        self.text.bind("<Configure>", self._on_cursor_activity)

        # Auto-indent
        self.text.bind("<Return>", self._auto_indent)

        # Context menu
        self._make_context_menu()

        # Apply fixed theme
        self.apply_theme()

        # Initial draw
        self.redraw_all()

    def _setup_tags(self):
        self.text.tag_configure("find_hit", background="#ffd966")
        self.text.tag_configure("find_current", background="#f4b183")
        self.text.tag_configure("current_line", background="#f2f2f2")
        self.text.tag_configure("bracket_match", background="#cfe2f3")

        # Whitespace highlighting
        self.text.tag_configure("ws_space", background="#f9f9f9")
        self.text.tag_configure("ws_tab", background="#f0f0ff")
        self.text.tag_configure("ws_eol", background="#f6f6f6")

        # Syntax highlighting
        self.text.tag_configure("syn_kw", foreground="#005cc5")
        self.text.tag_configure("syn_str", foreground="#a31515")
        self.text.tag_configure("syn_com", foreground="#6a737d")

    def _make_context_menu(self):
        self.ctx = tk.Menu(self.text, tearoff=0)
        self.ctx.add_command(label="Undo", command=self.undo)
        self.ctx.add_command(label="Redo", command=self.redo)
        self.ctx.add_separator()
        self.ctx.add_command(label="Cut", command=self.cut)
        self.ctx.add_command(label="Copy", command=self.copy)
        self.ctx.add_command(label="Paste", command=self.paste)
        self.ctx.add_separator()
        self.ctx.add_command(label="Select All", command=self.select_all)

        def popup(event):
            try:
                self.ctx.tk_popup(event.x_root, event.y_root)
            finally:
                self.ctx.grab_release()

        self.text.bind("<Button-3>", popup)              # Windows/Linux
        self.text.bind("<Control-Button-1>", popup)      # macOS fallback

    # ---------- Basic ops ----------
    def get_text(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set_dirty(self, is_dirty: bool):
        if self.dirty != is_dirty:
            self.dirty = is_dirty
            self.app.update_tab_title(self)
            self.app.update_status()

    def mark_saved(self):
        self.last_saved_hash = sha1(self.get_text())
        self.set_dirty(False)

    def undo(self):
        try:
            self.text.edit_undo()
        except Exception:
            pass

    def redo(self):
        try:
            self.text.edit_redo()
        except Exception:
            pass

    def cut(self):
        self.copy()
        try:
            self.text.delete("sel.first", "sel.last")
        except Exception:
            pass

    def copy(self):
        try:
            s = self.text.get("sel.first", "sel.last")
            self.text.clipboard_clear()
            self.text.clipboard_append(s)
        except Exception:
            pass

    def paste(self):
        try:
            s = self.text.clipboard_get()
            self.text.insert("insert", s)
        except Exception:
            pass

    def select_all(self):
        self.text.tag_add("sel", "1.0", "end-1c")
        self.text.focus_set()

    # ---------- Scroll / redraw ----------
    def _on_scrollbar(self, *args):
        self.text.yview(*args)
        self.redraw_all()

    def _on_textscroll(self, first, last):
        self.vsb.set(first, last)
        self.redraw_all()

    def redraw_all(self):
        self._draw_line_numbers()
        self._highlight_current_line()
        self._highlight_brackets()
        self._update_visible_highlighting()
        self._draw_minimap()
        self.app.update_status()

    def _visible_line_range(self):
        first_idx = self.text.index("@0,0")
        last_idx = self.text.index(f"@0,{self.text.winfo_height()}")
        first_line = int(first_idx.split(".")[0])
        last_line = int(last_idx.split(".")[0]) + 1
        return first_line, last_line

    def _draw_line_numbers(self):
        c = self.app.colors
        self.ln.delete("all")
        first_line, last_line = self._visible_line_range()

        bbox = self.text.bbox("1.0")
        line_h = bbox[3] if bbox else 18

        for line in range(first_line, last_line + 1):
            idx = f"{line}.0"
            info = self.text.dlineinfo(idx)
            if info is None:
                continue
            y0 = info[1]
            self.ln.create_text(
                45, y0 + line_h // 2,
                text=str(line),
                anchor="e",
                fill=c["line_num_fg"],
                font=(self.font_family, max(9, self.font_size - 1)),
            )

        self.ln.config(background=c["gutter_bg"])

    def _draw_minimap(self):
        c = self.app.colors
        self.minimap.delete("all")
        self.minimap.config(background=c["minimap_bg"])

        content = self.get_text()
        lines = content.splitlines() or [""]

        h = max(1, self.minimap.winfo_height())
        w = max(1, self.minimap.winfo_width())

        total = len(lines)
        if total <= 0:
            return

        first, last = self.text.yview()

        step = max(1, total // max(1, h))
        y = 0
        for i in range(0, total, step):
            s = lines[i]
            non_ws = len(s.strip())
            ratio = 0 if len(s) == 0 else min(1.0, non_ws / max(1, len(s)))
            x2 = int(5 + (w - 10) * ratio)
            self.minimap.create_line(5, y, x2, y, fill=c["minimap_fg"])
            y += 1
            if y >= h - 2:
                break

        y1 = int(first * h)
        y2 = int(last * h)
        self.minimap.create_rectangle(2, y1, w - 2, y2, outline=c["minimap_viewport"])

        def on_click(ev):
            frac = ev.y / max(1, h)
            self.text.yview_moveto(max(0.0, min(1.0, frac)))
            self.redraw_all()

        self.minimap.bind("<Button-1>", on_click)

    # ---------- Events ----------
    def _on_modified(self, _event=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            now_hash = sha1(self.get_text())
            self.set_dirty(now_hash != self.last_saved_hash)

    def _on_cursor_activity(self, _event=None):
        self.redraw_all()

    def _highlight_current_line(self):
        self.text.tag_remove("current_line", "1.0", "end")
        try:
            line = self.text.index("insert").split(".")[0]
            self.text.tag_add("current_line", f"{line}.0", f"{line}.0 lineend+1c")
        except Exception:
            pass

    def _highlight_brackets(self):
        self.text.tag_remove("bracket_match", "1.0", "end")

        pairs = {"(": ")", "[": "]", "{": "}", ")": "(", "]": "[", "}": "{"}
        opens = "([{"

        try:
            idx = self.text.index("insert")
            before = self.text.get("insert-1c", "insert")
            at = self.text.get("insert", "insert+1c")
            ch = before if before in pairs else (at if at in pairs else "")
            if not ch:
                return

            if ch in opens:
                start = idx if at == ch else self.text.index("insert-1c")
                match_idx = self._find_matching_forward(start, ch, pairs[ch])
                if match_idx:
                    self.text.tag_add("bracket_match", start, f"{start}+1c")
                    self.text.tag_add("bracket_match", match_idx, f"{match_idx}+1c")
            else:
                here = self.text.index("insert-1c") if before == ch else idx
                match_idx = self._find_matching_backward(here, pairs[ch], ch)
                if match_idx:
                    self.text.tag_add("bracket_match", match_idx, f"{match_idx}+1c")
                    self.text.tag_add("bracket_match", here, f"{here}+1c")
        except Exception:
            pass

    def _find_matching_forward(self, start_idx, open_ch, close_ch):
        depth = 0
        i = start_idx
        limit = 200000
        count = 0
        while count < limit:
            ch = self.text.get(i, f"{i}+1c")
            if not ch:
                break
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return i
            i = self.text.index(f"{i}+1c")
            count += 1
        return None

    def _find_matching_backward(self, start_idx, open_ch, close_ch):
        depth = 0
        i = start_idx
        limit = 200000
        count = 0
        while count < limit:
            ch = self.text.get(i, f"{i}+1c")
            if ch == close_ch:
                depth += 1
            elif ch == open_ch:
                depth -= 1
                if depth == 0:
                    return i
            if i == "1.0":
                break
            i = self.text.index(f"{i}-1c")
            count += 1
        return None

    def _auto_indent(self, event):
        try:
            line_start = self.text.index("insert linestart")
            line_end = self.text.index("insert lineend")
            line_text = self.text.get(line_start, line_end)

            indent = re.match(r"[ \t]*", line_text).group(0)  # type: ignore
            extra = ""
            if line_text.rstrip().endswith(":"):
                extra = " " * 4

            self.text.insert("insert", "\n" + indent + extra)
            return "break"
        except Exception:
            return None

    # ---------- Highlighting ----------
    def _update_visible_highlighting(self):
        first_line, last_line = self._visible_line_range()
        start = f"{first_line}.0"
        end = f"{last_line}.0 lineend"

        # Whitespace tags
        for tag in ("ws_space", "ws_tab", "ws_eol"):
            self.text.tag_remove(tag, start, end)

        if self.show_whitespace:
            for line in range(first_line, last_line + 1):
                ls = f"{line}.0"
                le = f"{line}.0 lineend"
                s = self.text.get(ls, le)

                for m in re.finditer(r"\t", s):
                    a = f"{line}.{m.start()}"
                    b = f"{line}.{m.start()+1}"
                    self.text.tag_add("ws_tab", a, b)

                for m in re.finditer(r" +", s):
                    a = f"{line}.{m.start()}"
                    b = f"{line}.{m.end()}"
                    self.text.tag_add("ws_space", a, b)

                try:
                    self.text.tag_add("ws_eol", le, f"{le}+1c")
                except Exception:
                    pass

        # Syntax tags
        for tag in ("syn_kw", "syn_str", "syn_com"):
            self.text.tag_remove(tag, start, end)

        if self.syntax_highlight:
            self._python_syntax_highlight_visible(first_line, last_line)

    def _python_syntax_highlight_visible(self, first_line, last_line):
        for line in range(first_line, last_line + 1):
            ls = f"{line}.0"
            le = f"{line}.0 lineend"
            s = self.text.get(ls, le)

            # strings (single-line, simple)
            for m in re.finditer(r"(\"[^\"]*\"|\'[^\']*\')", s):
                a = f"{line}.{m.start()}"
                b = f"{line}.{m.end()}"
                self.text.tag_add("syn_str", a, b)

            # comments
            m = re.search(r"#.*$", s)
            if m:
                a = f"{line}.{m.start()}"
                b = f"{line}.{m.end()}"
                self.text.tag_add("syn_com", a, b)

            # keywords
            for m in re.finditer(r"\b[A-Za-z_][A-Za-z_0-9]*\b", s):
                word = m.group(0)
                if word in PY_KEYWORDS:
                    a = f"{line}.{m.start()}"
                    b = f"{line}.{m.end()}"
                    self.text.tag_add("syn_kw", a, b)

    # ---------- Fixed theme ----------
    def apply_theme(self):
        c = self.app.colors
        self.text.config(
            background=c["text_bg"],
            foreground=c["text_fg"],
            insertbackground=c["caret"],
            selectbackground=c["select_bg"],
            selectforeground=c["select_fg"],
        )
        self.ln.config(background=c["gutter_bg"])
        self.minimap.config(background=c["minimap_bg"])


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1100x700")
        self.root.minsize(800, 500)

        # One fixed theme (no switching)
        self.colors = {
            "text_bg": "#ffffff",
            "text_fg": "#111111",
            "caret": "#111111",
            "select_bg": "#cfe8ff",
            "select_fg": "#111111",
            "gutter_bg": "#f5f5f5",
            "line_num_fg": "#666666",
            "minimap_bg": "#fafafa",
            "minimap_fg": "#999999",
            "minimap_viewport": "#4a86e8",
        }

        # State
        self.documents = []
        self.recent_files = []
        self.autosave_seconds = DEFAULT_AUTOSAVE_SECONDS
        self.save_on_focus_lost = tk.BooleanVar(value=False)

        # Autosave dir
        self.state_dir = Path.home() / ".simple_editorpp"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.autosave_dir = self.state_dir / "autosave"
        self.autosave_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "state.json"

        # Layout: left file tree + right notebook
        self.main = ttk.Frame(root)
        self.main.pack(fill="both", expand=True)

        self.left = ttk.Frame(self.main, width=250)
        self.left.pack(side="left", fill="y")

        self.right = ttk.Frame(self.main)
        self.right.pack(side="left", fill="both", expand=True)

        # Folder sidebar
        ttk.Label(self.left, text="Folder").pack(anchor="w", padx=8, pady=(8, 2))
        self.tree = ttk.Treeview(self.left, show="tree")
        self.tree.pack(fill="both", expand=True, padx=8, pady=6)
        self.tree.bind("<Double-1>", self._on_tree_open)

        btns = ttk.Frame(self.left)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Open Folder", command=self.open_folder).pack(side="left")
        ttk.Button(btns, text="Clear", command=self.clear_folder).pack(side="left", padx=6)

        # Notebook (tabs)
        self.notebook = ttk.Notebook(self.right)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self.update_status())

        # Status bar
        self.status_var = tk.StringVar(value="")
        self.status = ttk.Label(root, textvariable=self.status_var, anchor="w")
        self.status.pack(side="bottom", fill="x")

        # Menus
        self._build_menus()

        # Macro recorder
        self.macro = MacroRecorder()

        # Shortcuts
        self._bind_shortcuts()

        # Restore last state
        self._load_state()

        if not self.documents:
            self.new_file()

        self._autosave_tick()
        self.root.protocol("WM_DELETE_WINDOW", self.exit_app)

    # ---------- Menus ----------
    def _build_menus(self):
        self.menubar = tk.Menu(self.root)
        self.root.config(menu=self.menubar)

        # File
        self.filemenu = tk.Menu(self.menubar, tearoff=0)
        self.filemenu.add_command(label="New", command=self.new_file, accelerator="Ctrl+N")
        self.filemenu.add_command(label="Open...", command=self.open_file_dialog, accelerator="Ctrl+O")
        self.filemenu.add_command(label="Save", command=self.save_current, accelerator="Ctrl+S")
        self.filemenu.add_command(label="Save As...", command=self.save_current_as, accelerator="Ctrl+Shift+S")

        self.recent_menu = tk.Menu(self.filemenu, tearoff=0)
        self.filemenu.add_cascade(label="Open Recent", menu=self.recent_menu)
        self._rebuild_recent_menu()

        self.filemenu.add_separator()
        self.filemenu.add_command(label="Export HTML...", command=self.export_html)
        self.filemenu.add_command(label="Export PDF...", command=self.export_pdf)
        self.filemenu.add_separator()
        self.filemenu.add_command(label="Recover Autosave...", command=self.recover_autosave)
        self.filemenu.add_separator()
        self.filemenu.add_command(label="Close Tab", command=self.close_current_tab, accelerator="Ctrl+W")
        self.filemenu.add_command(label="Exit", command=self.exit_app, accelerator="Ctrl+Q")
        self.menubar.add_cascade(label="File", menu=self.filemenu)

        # Edit
        self.editmenu = tk.Menu(self.menubar, tearoff=0)
        self.editmenu.add_command(label="Undo", command=lambda: self.current_doc().undo(), accelerator="Ctrl+Z")
        self.editmenu.add_command(label="Redo", command=lambda: self.current_doc().redo(), accelerator="Ctrl+Y")
        self.editmenu.add_separator()
        self.editmenu.add_command(label="Cut", command=lambda: self.current_doc().cut(), accelerator="Ctrl+X")
        self.editmenu.add_command(label="Copy", command=lambda: self.current_doc().copy(), accelerator="Ctrl+C")
        self.editmenu.add_command(label="Paste", command=lambda: self.current_doc().paste(), accelerator="Ctrl+V")
        self.editmenu.add_separator()
        self.editmenu.add_command(label="Select All", command=lambda: self.current_doc().select_all(), accelerator="Ctrl+A")
        self.editmenu.add_separator()
        self.editmenu.add_command(label="Find...", command=self.find_dialog, accelerator="Ctrl+F")
        self.editmenu.add_command(label="Replace...", command=self.replace_dialog, accelerator="Ctrl+H")
        self.editmenu.add_command(label="Go to Line...", command=self.goto_line, accelerator="Ctrl+G")
        self.menubar.add_cascade(label="Edit", menu=self.editmenu)

        # View
        self.viewmenu = tk.Menu(self.menubar, tearoff=0)

        self.wrap_var = tk.BooleanVar(value=True)
        self.viewmenu.add_checkbutton(label="Word Wrap", variable=self.wrap_var, command=self.toggle_wrap)

        self.ws_var = tk.BooleanVar(value=False)
        self.viewmenu.add_checkbutton(label="Show Whitespace (highlight)", variable=self.ws_var, command=self.toggle_whitespace)

        self.syn_var = tk.BooleanVar(value=True)
        self.viewmenu.add_checkbutton(label="Syntax Highlight (Python)", variable=self.syn_var, command=self.toggle_syntax)

        self.viewmenu.add_separator()
        self.viewmenu.add_command(label="Font...", command=self.font_picker)
        self.viewmenu.add_command(label="Zoom In", command=lambda: self.zoom(1), accelerator="Ctrl++")
        self.viewmenu.add_command(label="Zoom Out", command=lambda: self.zoom(-1), accelerator="Ctrl+-")
        self.viewmenu.add_command(label="Reset Zoom", command=lambda: self.zoom(reset=True), accelerator="Ctrl+0")
        self.menubar.add_cascade(label="View", menu=self.viewmenu)

        # Options
        self.optmenu = tk.Menu(self.menubar, tearoff=0)
        self.optmenu.add_checkbutton(label="Save on focus lost", variable=self.save_on_focus_lost)
        self.optmenu.add_command(label="Set autosave interval...", command=self.set_autosave_interval)
        self.menubar.add_cascade(label="Options", menu=self.optmenu)

        # Macros
        self.macromenu = tk.Menu(self.menubar, tearoff=0)
        self.macromenu.add_command(label="Start Recording", command=self.macro_start)
        self.macromenu.add_command(label="Stop Recording", command=self.macro_stop)
        self.macromenu.add_command(label="Play Macro", command=self.macro_play)
        self.menubar.add_cascade(label="Macros", menu=self.macromenu)

    # ---------- Shortcuts ----------
    def _bind_shortcuts(self):
        r = self.root
        r.bind_all("<Control-n>", lambda e: self.new_file())
        r.bind_all("<Control-o>", lambda e: self.open_file_dialog())
        r.bind_all("<Control-s>", lambda e: self.save_current())
        r.bind_all("<Control-Shift-S>", lambda e: self.save_current_as())
        r.bind_all("<Control-q>", lambda e: self.exit_app())
        r.bind_all("<Control-w>", lambda e: self.close_current_tab())

        r.bind_all("<Control-f>", lambda e: self.find_dialog())
        r.bind_all("<Control-h>", lambda e: self.replace_dialog())
        r.bind_all("<Control-g>", lambda e: self.goto_line())

        r.bind_all("<Control-plus>", lambda e: self.zoom(1))
        r.bind_all("<Control-equal>", lambda e: self.zoom(1))   # some keyboards
        r.bind_all("<Control-minus>", lambda e: self.zoom(-1))
        r.bind_all("<Control-0>", lambda e: self.zoom(reset=True))

        # Macro record key capture
        r.bind_all("<KeyPress>", self._macro_keypress, add=True)

    def _macro_keypress(self, event):
        doc = self.current_doc(safe=True)
        if not doc:
            return
        if self.root.focus_get() == doc.text:
            self.macro.record_key(event)

    # ---------- Documents ----------
    def current_doc(self, safe=False):
        if not self.documents:
            return None if safe else (_ for _ in ()).throw(RuntimeError("No documents"))
        tab = self.notebook.select()
        for doc in self.documents:
            if str(doc.frame) == str(tab):
                return doc
        return self.documents[0]

    def new_file(self):
        doc = Document(self, filepath=None, initial_text="")
        self.documents.append(doc)
        self.notebook.add(doc.frame, text="Untitled")
        self.notebook.select(doc.frame)

        doc.text.bind("<FocusOut>", lambda e, d=doc: self._focus_lost_save(d), add=True)

        self.update_status()
        self._save_state()

    def open_file_dialog(self):
        path = filedialog.askopenfilename(
            filetypes=[("Text files", "*.txt;*.py;*.md;*.json;*.csv;*.log"), ("All files", "*.*")]
        )
        if not path:
            return
        self.open_file(path)

    def open_file(self, path: str):
        for doc in self.documents:
            if doc.filepath and os.path.abspath(doc.filepath) == os.path.abspath(path):
                self.notebook.select(doc.frame)
                return

        if not self.confirm_discard_if_needed():
            return

        try:
            content = safe_read_text(path)
        except Exception:
            messagebox.showerror("Oops!", "Unable to open file.")
            return

        doc = Document(self, filepath=path, initial_text=content)
        self.documents.append(doc)
        self.notebook.add(doc.frame, text=self._tab_title(doc))
        self.notebook.select(doc.frame)

        doc.mark_saved()
        self.add_recent(path)

        doc.text.bind("<FocusOut>", lambda e, d=doc: self._focus_lost_save(d), add=True)

        self.update_status()
        self._save_state()

    def close_current_tab(self):
        doc = self.current_doc(safe=True)
        if not doc:
            return
        if doc.dirty:
            res = messagebox.askyesnocancel("Unsaved changes", "Save before closing this tab?")
            if res is None:
                return
            if res is True:
                if not self.save_doc(doc):
                    return

        self.notebook.forget(doc.frame)
        self.documents.remove(doc)

        if not self.documents:
            self.new_file()

        self.update_status()
        self._save_state()

    # ---------- Save / load ----------
    def save_current(self):
        doc = self.current_doc()
        self.save_doc(doc)

    def save_current_as(self):
        doc = self.current_doc()
        self.save_doc_as(doc)

    def save_doc(self, doc: Document) -> bool:
        if not doc.filepath:
            return self.save_doc_as(doc)
        try:
            safe_write_text(doc.filepath, doc.get_text())
            doc.mark_saved()
            self.update_tab_title(doc)
            self.add_recent(doc.filepath)
            self.update_status()
            self._save_state()
            return True
        except Exception:
            messagebox.showerror("Oops!", "Unable to save file.")
            return False

    def save_doc_as(self, doc: Document) -> bool:
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("Python", "*.py"), ("Markdown", "*.md"), ("All files", "*.*")]
        )
        if not path:
            return False
        doc.filepath = path
        ok = self.save_doc(doc)
        if ok:
            self.update_tab_title(doc)
        return ok

    def confirm_discard_if_needed(self) -> bool:
        doc = self.current_doc(safe=True)
        if not doc or not doc.dirty:
            return True
        res = messagebox.askyesnocancel("Unsaved changes", "You have unsaved changes. Save before continuing?")
        if res is None:
            return False
        if res is True:
            return self.save_current() is not False
        return True

    # ---------- Recent files ----------
    def add_recent(self, path: str):
        path = os.path.abspath(path)
        self.recent_files = [p for p in self.recent_files if p != path]
        self.recent_files.insert(0, path)
        self.recent_files = self.recent_files[:MAX_RECENT]
        self._rebuild_recent_menu()
        self._save_state()

    def _rebuild_recent_menu(self):
        self.recent_menu.delete(0, tk.END)
        if not self.recent_files:
            self.recent_menu.add_command(label="(empty)", state=tk.DISABLED)
            return
        for p in self.recent_files:
            self.recent_menu.add_command(label=p, command=lambda x=p: self.open_file(x))

    # ---------- Find / Replace / Go-to ----------
    def _clear_find_tags(self, doc: Document):
        doc.text.tag_remove("find_hit", "1.0", "end")
        doc.text.tag_remove("find_current", "1.0", "end")

    def find_dialog(self):
        doc = self.current_doc()
        win = tk.Toplevel(self.root)
        win.title("Find")
        win.transient(self.root)
        win.resizable(False, False)

        ttk.Label(win, text="Find:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        entry = ttk.Entry(win, width=40)
        entry.grid(row=0, column=1, padx=8, pady=8)
        entry.focus_set()

        case_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(win, text="Case sensitive", variable=case_var).grid(row=1, column=1, padx=8, sticky="w")

        def do_find(next_hit=False):
            self._clear_find_tags(doc)
            needle = entry.get()
            if not needle:
                return

            start = doc.text.index("insert") if next_hit else "1.0"
            idx = doc.text.search(
                needle, start, stopindex="end",
                nocase=(0 if case_var.get() else 1)
            )

            content = doc.get_text()
            flags = 0 if case_var.get() else re.IGNORECASE
            try:
                for m in re.finditer(re.escape(needle), content, flags):
                    a = doc.text.index(f"1.0+{m.start()}c")
                    b = doc.text.index(f"1.0+{m.end()}c")
                    doc.text.tag_add("find_hit", a, b)
            except Exception:
                pass

            if idx:
                end = f"{idx}+{len(needle)}c"
                doc.text.tag_add("find_current", idx, end)
                doc.text.mark_set("insert", end)
                doc.text.see(idx)
                doc.redraw_all()

        ttk.Button(win, text="Find", command=lambda: do_find(False)).grid(row=2, column=0, padx=8, pady=8)
        ttk.Button(win, text="Find Next", command=lambda: do_find(True)).grid(row=2, column=1, padx=8, pady=8, sticky="w")

        def on_close():
            self._clear_find_tags(doc)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

    def replace_dialog(self):
        doc = self.current_doc()
        win = tk.Toplevel(self.root)
        win.title("Replace")
        win.transient(self.root)
        win.resizable(False, False)

        ttk.Label(win, text="Find:").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        find_e = ttk.Entry(win, width=40)
        find_e.grid(row=0, column=1, padx=8, pady=6)
        find_e.focus_set()

        ttk.Label(win, text="Replace:").grid(row=1, column=0, padx=8, pady=6, sticky="w")
        rep_e = ttk.Entry(win, width=40)
        rep_e.grid(row=1, column=1, padx=8, pady=6)

        case_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(win, text="Case sensitive", variable=case_var).grid(row=2, column=1, padx=8, sticky="w")

        def replace_one():
            needle = find_e.get()
            repl = rep_e.get()
            if not needle:
                return
            idx = doc.text.search(
                needle, "insert", stopindex="end",
                nocase=(0 if case_var.get() else 1)
            )
            if not idx:
                return
            end = f"{idx}+{len(needle)}c"
            doc.text.delete(idx, end)
            doc.text.insert(idx, repl)
            doc.text.mark_set("insert", f"{idx}+{len(repl)}c")
            doc.text.see(idx)
            doc.redraw_all()

        def replace_all():
            needle = find_e.get()
            repl = rep_e.get()
            if not needle:
                return
            content = doc.get_text()
            if case_var.get():
                new = content.replace(needle, repl)
            else:
                new = re.sub(re.escape(needle), repl, content, flags=re.IGNORECASE)
            doc.text.delete("1.0", "end")
            doc.text.insert("1.0", new)
            doc.redraw_all()

        ttk.Button(win, text="Replace", command=replace_one).grid(row=3, column=0, padx=8, pady=8)
        ttk.Button(win, text="Replace All", command=replace_all).grid(row=3, column=1, padx=8, pady=8, sticky="w")

        def on_close():
            self._clear_find_tags(doc)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

    def goto_line(self):
        doc = self.current_doc()
        n = simpledialog.askinteger("Go to Line", "Line number:", minvalue=1)
        if not n:
            return
        idx = f"{n}.0"
        try:
            doc.text.mark_set("insert", idx)
            doc.text.see(idx)
            doc.redraw_all()
        except Exception:
            pass

    # ---------- View toggles ----------
    def toggle_wrap(self):
        doc = self.current_doc()
        doc.text.config(wrap=tk.WORD if self.wrap_var.get() else tk.NONE)
        doc.redraw_all()

    def toggle_whitespace(self):
        doc = self.current_doc()
        doc.show_whitespace = self.ws_var.get()
        doc.redraw_all()

    def toggle_syntax(self):
        doc = self.current_doc()
        doc.syntax_highlight = self.syn_var.get()
        doc.redraw_all()

    def font_picker(self):
        doc = self.current_doc()

        win = tk.Toplevel(self.root)
        win.title("Font")
        win.transient(self.root)
        win.resizable(False, False)

        families = sorted(tkfont.families())
        sizes = list(range(8, 29))

        ttk.Label(win, text="Family:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        fam = ttk.Combobox(win, values=families, width=30, state="readonly")
        fam.set(doc.font_family)
        fam.grid(row=0, column=1, padx=8, pady=8)

        ttk.Label(win, text="Size:").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        size = ttk.Combobox(win, values=sizes, width=10, state="readonly")
        size.set(doc.font_size)
        size.grid(row=1, column=1, padx=8, pady=8, sticky="w")

        def apply():
            doc.font_family = fam.get()
            doc.font_size = int(size.get())
            doc.font.config(family=doc.font_family, size=doc.font_size)
            doc.redraw_all()

        ttk.Button(win, text="Apply", command=apply).grid(row=2, column=0, padx=8, pady=8)
        ttk.Button(win, text="Close", command=win.destroy).grid(row=2, column=1, padx=8, pady=8, sticky="w")

    def zoom(self, delta=0, reset=False):
        doc = self.current_doc()
        if reset:
            doc.font_size = 12
        else:
            doc.font_size = max(8, min(30, doc.font_size + delta))
        doc.font.config(size=doc.font_size)
        doc.redraw_all()

    # ---------- Folder sidebar ----------
    def clear_folder(self):
        self.tree.delete(*self.tree.get_children())

    def open_folder(self):
        folder = filedialog.askdirectory()
        if not folder:
            return
        self.clear_folder()
        self._populate_tree("", folder)

    def _populate_tree(self, parent_id, folder_path):
        try:
            entries = sorted(Path(folder_path).iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except Exception:
            return

        for p in entries:
            node = self.tree.insert(parent_id, "end", text=p.name, values=(str(p),))
            if p.is_dir():
                self.tree.insert(node, "end", text="(loading...)")
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_expand)

    def _on_tree_expand(self, _event):
        item = self.tree.focus()
        if not item:
            return
        vals = self.tree.item(item, "values")
        if not vals:
            return
        path = vals[0]
        p = Path(path)
        if not p.is_dir():
            return

        kids = self.tree.get_children(item)
        if len(kids) == 1 and self.tree.item(kids[0], "text") == "(loading...)":
            self.tree.delete(kids[0])
            self._populate_tree(item, str(p))

    def _on_tree_open(self, _event):
        item = self.tree.focus()
        if not item:
            return
        vals = self.tree.item(item, "values")
        if not vals:
            return
        path = vals[0]
        if Path(path).is_file():
            self.open_file(path)

    # ---------- Export ----------
    def export_html(self):
        doc = self.current_doc()
        path = filedialog.asksaveasfilename(defaultextension=".html", filetypes=[("HTML", "*.html")])
        if not path:
            return
        text = doc.get_text()
        esc = (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
        )
        html = f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>{doc.filepath or "Untitled"}</title>
<style>
body {{ font-family: monospace; background: #fff; color: #111; }}
pre {{ white-space: pre-wrap; word-wrap: break-word; }}
</style></head>
<body><pre>{esc}</pre></body></html>"""
        try:
            safe_write_text(path, html)
        except Exception:
            messagebox.showerror("Oops!", "Unable to export HTML.")

    def export_pdf(self):
        if not REPORTLAB_OK:
            messagebox.showerror("PDF Export", "reportlab not installed; PDF export unavailable.")
            return
        doc = self.current_doc()
        path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not path:
            return

        lines = doc.get_text().splitlines()
        c = rl_canvas.Canvas(path, pagesize=letter)
        width, height = letter

        x = 40
        y = height - 50
        line_h = 14

        c.setFont("Courier", 10)
        for line in lines:
            if y < 50:
                c.showPage()
                c.setFont("Courier", 10)
                y = height - 50
            c.drawString(x, y, line[:200])
            y -= line_h

        try:
            c.save()
        except Exception:
            messagebox.showerror("Oops!", "Unable to export PDF.")

    # ---------- Autosave + recovery ----------
    def _autosave_tick(self):
        for doc in self.documents:
            if not doc.dirty:
                continue
            snapshot = {
                "time": time.time(),
                "filepath": doc.filepath,
                "autosave_id": doc.autosave_id,
                "text": doc.get_text(),
            }
            try:
                p = self.autosave_dir / f"{doc.autosave_id}.json"
                p.write_text(json.dumps(snapshot), encoding="utf-8")
            except Exception:
                pass

        self.root.after(int(self.autosave_seconds * 1000), self._autosave_tick)

    def recover_autosave(self):
        files = sorted(self.autosave_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            messagebox.showinfo("Recover", "No autosave snapshots found.")
            return

        p = files[0]
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            text = data.get("text", "")
            fp = data.get("filepath", None)
        except Exception:
            messagebox.showerror("Recover", "Autosave snapshot unreadable.")
            return

        doc = Document(self, filepath=fp, initial_text=text)
        self.documents.append(doc)
        self.notebook.add(doc.frame, text=self._tab_title(doc) + " (Recovered)")
        self.notebook.select(doc.frame)

        doc.set_dirty(True)
        doc.text.bind("<FocusOut>", lambda e, d=doc: self._focus_lost_save(d), add=True)

        self.update_status()

    def set_autosave_interval(self):
        n = simpledialog.askinteger("Autosave", "Autosave interval (seconds):", minvalue=5, maxvalue=600)
        if n:
            self.autosave_seconds = int(n)

    # ---------- Save on focus lost ----------
    def _focus_lost_save(self, doc: Document):
        if not self.save_on_focus_lost.get():
            return
        if doc.dirty and doc.filepath:
            self.save_doc(doc)

    # ---------- Macros ----------
    def macro_start(self):
        self.macro.start()
        self.update_status(extra="Macro recording: ON")

    def macro_stop(self):
        self.macro.stop()
        self.update_status(extra="Macro recording: OFF")

    def macro_play(self):
        doc = self.current_doc()
        self.macro.play(doc.text)
        doc.redraw_all()

    # ---------- Exit ----------
    def exit_app(self):
        for doc in list(self.documents):
            if doc.dirty:
                self.notebook.select(doc.frame)
                res = messagebox.askyesnocancel("Unsaved changes", f"Save changes to {doc.filepath or 'Untitled'}?")
                if res is None:
                    return
                if res is True:
                    if not self.save_doc(doc):
                        return
        self._save_state()
        self.root.destroy()

    # ---------- Tab title / status ----------
    def _tab_title(self, doc: Document):
        name = os.path.basename(doc.filepath) if doc.filepath else "Untitled"
        return name + (" *" if doc.dirty else "")

    def update_tab_title(self, doc: Document):
        try:
            idx = self.notebook.index(doc.frame)
            self.notebook.tab(idx, text=self._tab_title(doc))
        except Exception:
            pass

    def update_status(self, extra=""):
        doc = self.current_doc(safe=True)
        if not doc:
            self.status_var.set(extra)
            return
        idx = doc.text.index("insert")
        line, col = idx.split(".")
        line = int(line)
        col = int(col) + 1
        name = doc.filepath if doc.filepath else "Untitled"
        state = "Unsaved" if doc.dirty else "Saved"
        chars = len(doc.get_text())
        msg = f"{name} | {state} | Ln {line}, Col {col} | {chars} chars"
        if extra:
            msg += f" | {extra}"
        self.status_var.set(msg)

    # ---------- State ----------
    def _save_state(self):
        try:
            data = {
                "recent_files": self.recent_files,
                "autosave_seconds": self.autosave_seconds,
                "save_on_focus_lost": bool(self.save_on_focus_lost.get()),
            }
            self.state_file.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _load_state(self):
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.recent_files = data.get("recent_files", [])[:MAX_RECENT]
            self.autosave_seconds = int(data.get("autosave_seconds", DEFAULT_AUTOSAVE_SECONDS))
            self.save_on_focus_lost.set(bool(data.get("save_on_focus_lost", False)))
            self._rebuild_recent_menu()
        except Exception:
            pass


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
