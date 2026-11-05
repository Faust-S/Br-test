"""Second, independent Linux/X11 GUI host for native U04 reattachment qualification.

The first host GUI has already closed. A NEW Python process owns this visible
Tk host, obtains explicit process approval, and performs focus via X11Port.
Mouse clicks come from the external qualification controller using XTEST.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tkinter as tk

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location("iwxa_u04_x11",ROOT/"u04-presentation"/"native"/"linux_x11.py")
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

def main():
    if len(sys.argv)!=4:
        raise SystemExit("EXPECTED_PID_STAMP_RECEIPT")
    pid=int(sys.argv[1])
    stamp=sys.argv[2]
    receipt=Path(sys.argv[3])
    def approve(app_ref,host_ref,request):
        good=(app_ref=="editor" and host_ref=="gui-host" and
              request.get("operation")=="reattach" and
              request.get("pid")==pid and request.get("stamp")==stamp)
        return {"approved":good,"pid":pid,"stamp":stamp}
    port=module.X11Port(approve)
    binding=port.reattach("editor","gui-host",pid,stamp)
    if binding.get("status") not in ("WINDOW_OBSERVED","BOUND_NO_WINDOW") or (
        binding.get("spawned") is not False
    ):
        raise SystemExit("SECOND_HOST_REATTACH_REJECTED_"+str(binding.get("status")))
    gui=tk.Tk()
    gui.title("IWXA U04 Reattached X11 Host")
    gui.geometry("350x180+600+50")
    status=tk.StringVar(value="SECOND HOST READY")
    tk.Label(gui,textvariable=status).pack()
    def act(operation):
        if operation=="focus":
            result=port.activate(binding["bindingRef"],"gui-host")
        else:
            result=port.close_binding(binding["bindingRef"],"gui-host")
        evidence={
            "schema":"IWXA_U04_X11_SECOND_HOST/1",
            "host_pid":os.getpid(),
            "operation":operation,
            "status":result.get("status"),
            "focused":result.get("focused",False),
            "processTerminated":result.get("processTerminated"),
            "processId":pid,
            "processStamp":stamp,
            "spawned":False,
            "windowRef":result.get("windowRef")
        }
        tmp=receipt.with_name(receipt.name+".tmp")
        tmp.write_text(json.dumps(evidence),encoding="utf-8")
        tmp.replace(receipt)
        status.set(result["status"])
    tk.Button(gui,text="Focus existing",width=23,command=lambda:act("focus")).pack()
    tk.Button(gui,text="Detach only",width=23,command=lambda:act("detach")).pack()
    tk.Button(gui,text="Close host GUI",width=23,command=gui.destroy).pack()
    gui.mainloop()

if __name__=="__main__":
    main()
