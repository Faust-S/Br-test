"""Real independent editable Windows GUI fixture (pythonw, no console)."""
import sys
from pathlib import Path
import tkinter as tk
import os
import subprocess
import time

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

# Tk on Linux may omit the _NET_WM_PID hint. Publish it explicitly on the
# actual mapped toplevel so the existing X11 observer can match this real PID.
# This is fixture metadata, not an authorization grant to an arbitrary window.
if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
    root.update()
    for attempt in range(20):
        found=subprocess.run(["xdotool","search","--name",
                              "^IWXA U04 Real External Editor$"],
                             capture_output=True,text=True,timeout=4)
        if found.returncode == 0 and found.stdout.strip():
            for xid in found.stdout.splitlines():
                if xid.isdecimal():
                    subprocess.run(["xprop","-id",xid,"-f","_NET_WM_PID",
                                    "32c","-set","_NET_WM_PID",str(os.getpid())],
                                   check=True,capture_output=True,timeout=4)
            break
        root.update()
        time.sleep(.1)
root.mainloop()
