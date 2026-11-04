"""Real independent editable Windows GUI fixture (pythonw, no console)."""
import sys
from pathlib import Path
import tkinter as tk

document = Path(sys.argv[1])
root = tk.Tk()
root.title("IWXA U04 Real External Editor")
root.geometry("520x320")
text = tk.Text(root)
text.pack(fill="both", expand=True)
text.insert("1.0", document.read_text(encoding="utf-8"))

def save():
    document.write_text(text.get("1.0", "end-1c"), encoding="utf-8")

tk.Button(root, text="Save user document", command=save).pack()
root.protocol("WM_DELETE_WINDOW", lambda: (save(), root.destroy()))
root.mainloop()
