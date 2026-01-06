from tkinter import *
from tkinter.filedialog import askopenfilename, asksaveasfilename
from tkinter.messagebox import showerror

filename = None

def newFile():
    global filename
    filename = None
    text.delete("1.0", END)

def saveFile():
    global filename
    if not filename:
        saveAs()
        return
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text.get("1.0", END))
    except:
        showerror(title="Oops!", message="Unable to save file...")

def saveAs():
    global filename
    fname = asksaveasfilename(defaultextension=".txt",
                              filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
    if not fname:
        return
    filename = fname
    saveFile()

def openFile():
    global filename
    fname = askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
    if not fname:
        return
    filename = fname
    try:
        with open(filename, "r", encoding="utf-8") as f:
            t = f.read()
        text.delete("1.0", END)
        text.insert("1.0", t)
    except:
        showerror(title="Oops!", message="Unable to open file...")

root = Tk()
root.title("Simple Text Editor")
root.minsize(width=400, height=400)
root.maxsize(width=800, height=800)

text = Text(root)
text.pack(expand=True, fill=BOTH)

menubar = Menu(root)
filemenu = Menu(menubar, tearoff=0)
filemenu.add_command(label="New", command=newFile)
filemenu.add_command(label="Open", command=openFile)
filemenu.add_command(label="Save", command=saveFile)
filemenu.add_command(label="Save As", command=saveAs)
filemenu.add_separator()
filemenu.add_command(label="Exit", command=root.quit)
menubar.add_cascade(label="File", menu=filemenu)

root.config(menu=menubar)
root.mainloop()
