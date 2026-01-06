import tkinter as tk
from tkinter import filedialog, messagebox

filename = None
dirty = False
recent_files = []
MAX_RECENT = 10

def set_title():
    name = filename if filename else "Untitled"
    mark = "*" if dirty else ""
    root.title(f"{name}{mark} - Simple Text Editor")

def update_status(_event=None):
    # Cursor position
    try:
        idx = text.index("insert")  # "line.col"
        line, col = idx.split(".")
        line, col = int(line), int(col) + 1
    except:
        line, col = 1, 1

    # Char count (excluding trailing newline Tk adds at END)
    content = text.get("1.0", "end-1c")
    chars = len(content)

    name = filename if filename else "Untitled"
    state = "Unsaved" if dirty else "Saved"
    status_var.set(f"{name} | {state} | Ln {line}, Col {col} | {chars} chars")

def on_modified(_event=None):
    global dirty
    if text.edit_modified():
        dirty = True
        text.edit_modified(False)
        set_title()
        update_status()

def confirm_discard_changes():
    if not dirty:
        return True
    resp = messagebox.askyesnocancel(
        "Unsaved changes",
        "You have unsaved changes. Save before continuing?"
    )
    if resp is None:   # Cancel
        return False
    if resp is True:   # Yes -> save
        return saveFile()
    return True        # No -> discard

def add_to_recent(path):
    global recent_files
    if not path:
        return
    # move to front, remove duplicates
    recent_files = [p for p in recent_files if p != path]
    recent_files.insert(0, path)
    recent_files = recent_files[:MAX_RECENT]
    rebuild_recent_menu()

def rebuild_recent_menu():
    recent_menu.delete(0, tk.END)
    if not recent_files:
        recent_menu.add_command(label="(empty)", state=tk.DISABLED)
        return
    for path in recent_files:
        recent_menu.add_command(
            label=path,
            command=lambda p=path: openFile(p)
        )

def newFile():
    global filename, dirty
    if not confirm_discard_changes():
        return
    filename = None
    text.delete("1.0", tk.END)
    dirty = False
    set_title()
    update_status()

def saveFile():
    global filename, dirty
    if not filename:
        return saveAs()
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text.get("1.0", "end-1c"))
        dirty = False
        set_title()
        update_status()
        add_to_recent(filename)
        return True
    except:
        messagebox.showerror("Oops!", "Unable to save file...")
        return False

def saveAs():
    global filename, dirty
    path = filedialog.asksaveasfilename(
        defaultextension=".txt",
        filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
    )
    if not path:
        return False
    filename = path
    ok = saveFile()
    if ok:
        dirty = False
        set_title()
        update_status()
        add_to_recent(filename)
    return ok

def openFile(path=None):
    global filename, dirty
    if not confirm_discard_changes():
        return

    if path is None:
        path = filedialog.askopenfilename(
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if not path:
            return

    try:
        with open(path, "r", encoding="utf-8") as f:
            t = f.read()
        filename = path
        text.delete("1.0", tk.END)
        text.insert("1.0", t)
        dirty = False
        text.edit_modified(False)
        set_title()
        update_status()
        add_to_recent(filename)
    except:
        messagebox.showerror("Oops!", "Unable to open file...")

def clearText():
    global dirty
    if not confirm_discard_changes():
        return
    text.delete("1.0", tk.END)
    dirty = False
    set_title()
    update_status()

def selectAll():
    text.tag_add("sel", "1.0", "end-1c")
    text.focus_set()
    update_status()

def toggle_wrap():
    if wrap_var.get():
        text.config(wrap=tk.WORD)
    else:
        text.config(wrap=tk.NONE)
    update_status()

def on_exit():
    if confirm_discard_changes():
        root.destroy()

# ---- UI ----
root = tk.Tk()
root.title("Simple Text Editor")
root.minsize(width=400, height=400)
root.maxsize(width=800, height=800)

# Text + Scrollbar
frame = tk.Frame(root)
frame.pack(expand=True, fill=tk.BOTH)

scroll = tk.Scrollbar(frame)
scroll.pack(side=tk.RIGHT, fill=tk.Y)

text = tk.Text(frame, wrap=tk.WORD, undo=True, yscrollcommand=scroll.set)
text.pack(expand=True, fill=tk.BOTH)
scroll.config(command=text.yview)

# Status bar
status_var = tk.StringVar()
status = tk.Label(root, textvariable=status_var, anchor="w")
status.pack(side=tk.BOTTOM, fill=tk.X)

# Menu
menubar = tk.Menu(root)
filemenu = tk.Menu(menubar, tearoff=0)

recent_menu = tk.Menu(filemenu, tearoff=0)
filemenu.add_command(label="New", command=newFile)
filemenu.add_command(label="Open", command=openFile)
filemenu.add_command(label="Save", command=saveFile)
filemenu.add_command(label="Save As", command=saveAs)
filemenu.add_cascade(label="Open Recent", menu=recent_menu)

filemenu.add_separator()
filemenu.add_command(label="Exit", command=on_exit)

editmenu = tk.Menu(menubar, tearoff=0)
editmenu.add_command(label="Select All", command=selectAll)
editmenu.add_command(label="Clear", command=clearText)

viewmenu = tk.Menu(menubar, tearoff=0)
wrap_var = tk.BooleanVar(value=True)
viewmenu.add_checkbutton(label="Word Wrap", onvalue=True, offvalue=False,
                         variable=wrap_var, command=toggle_wrap)

menubar.add_cascade(label="File", menu=filemenu)
menubar.add_cascade(label="Edit", menu=editmenu)
menubar.add_cascade(label="View", menu=viewmenu)

root.config(menu=menubar)

# Events
text.bind("<<Modified>>", on_modified)
text.bind("<KeyRelease>", update_status)
text.bind("<ButtonRelease-1>", update_status)
root.protocol("WM_DELETE_WINDOW", on_exit)

# Init recent + status
rebuild_recent_menu()
set_title()
update_status()

root.mainloop()
