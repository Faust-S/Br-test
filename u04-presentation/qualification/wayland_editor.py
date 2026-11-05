"""A genuine native Wayland GTK3 window, used only for CI evidence.

This fixture speaks to a Weston compositor through the GDK Wayland backend.
It deliberately does not expose a foreign window ID or promise focus.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

receipt=Path(sys.argv[1])
document=Path(sys.argv[2])
window=Gtk.Window(title="IWXA U04 Native Wayland Editor")
window.set_default_size(480,260)
box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
window.add(box)
entry=Gtk.TextView()
entry.get_buffer().set_text(document.read_text(encoding="utf-8"))
box.pack_start(entry,True,True,0)
def save(*_):
    buffer=entry.get_buffer()
    document.write_text(buffer.get_text(buffer.get_start_iter(),buffer.get_end_iter(),True),
                        encoding="utf-8")
button=Gtk.Button(label="Save user document")
button.connect("clicked",save)
box.pack_start(button,False,False,0)
window.connect("destroy",Gtk.main_quit)
window.show_all()

def report():
    display=Gdk.Display.get_default()
    native_type=type(display).__name__ if display else "None"
    mapped=window.get_mapped()
    valid=("Wayland" in native_type or "wayland" in native_type.lower()) and mapped
    record={"schema":"IWXA_U04_WAYLAND_GTK_FIXTURE/1",
            "pid":os.getpid(),"native_backend":native_type,"mapped":bool(mapped),
            "windowRef":None,"native_wayland":bool(valid)}
    if valid:
        tmp=receipt.with_suffix(".tmp")
        tmp.write_text(json.dumps(record),encoding="utf-8")
        tmp.replace(receipt)
        return False
    return True
GLib.timeout_add(120,report)
Gtk.main()
