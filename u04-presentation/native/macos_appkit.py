"""Native macOS program presentation via AppKit NSRunningApplication.

No Accessibility permission requests; no unsupported window references or embedding.
The native port must be host-provided/compiled from appkit_process.swift.
"""
from pathlib import Path
import os
import subprocess
import uuid

SCHEMA="IWXA_PRESENTATION_BRIDGE/1.0.0"

class MacOSPort:
    def __init__(self, native, resolver, *, run=subprocess.run, platform=None):
        if not callable(resolver):
            raise ValueError("HOST_APPROVAL_REQUIRED")
        self.native=Path(native)
        self.resolver=resolver
        self.run=run
        self.platform=platform if platform is not None else os.sys.platform
        self.bindings={}

    def _supported(self):
        return self.platform=="darwin" and self.native.is_file()

    def capabilities(self):
        supported=self._supported()
        reason="APPKIT_PORT_UNAVAILABLE"
        return {"schema":SCHEMA,"capabilities":[
            {"mode":mode,"supported":supported,**({} if supported else {"reason":reason})}
            for mode in ("LAUNCH","FOCUS")
        ] + [
            {"mode":"LINKED_WINDOW","supported":False,
             "reason":"NATIVE_WINDOW_REFERENCE_UNAVAILABLE"},
            {"mode":"EMBEDDED_VIEW","supported":False,
             "reason":"NO_OFFICIAL_TOOLKIT_EMBED_API"}]}

    def _call(self,command,pid):
        if not self._supported():
            return {"status":"PLATFORM_UNSUPPORTED"}
        try:
            result=self.run([str(self.native),command,str(pid)],text=True,
                            capture_output=True,timeout=10,check=False)
            if result.returncode not in (0,2):
                return {"status":"NATIVE_PORT_FAILED"}
            import json
            return json.loads(result.stdout)
        except (OSError,ValueError,subprocess.TimeoutExpired):
            return {"status":"NATIVE_PORT_FAILED"}

    def open(self,app_ref,host_ref,launch_spec):
        if not self._supported():
            return {"schema":SCHEMA,"status":"PLATFORM_UNSUPPORTED"}
        approved=self.resolver(app_ref,host_ref,launch_spec)
        if not isinstance(approved,dict) or approved.get("approved") is not True:
            return {"schema":SCHEMA,"status":"HOST_APPROVAL_REQUIRED"}
        exe,args=approved.get("executable"),approved.get("args",[])
        if not isinstance(exe,str) or not Path(exe).is_absolute() or not Path(exe).is_file() or (
            not isinstance(args,list) or len(args)>64 or any(
               not isinstance(a,str) or "\0" in a or len(a)>16384 for a in args
            )
        ):
            return {"schema":SCHEMA,"status":"EXECUTABLE_INVALID"}
        try:
            process=subprocess.Popen([exe,*args],stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        except OSError:
            return {"schema":SCHEMA,"status":"LAUNCH_FAILED"}
        observed=self._call("inspect",process.pid)
        if observed.get("status")!="PROCESS_OBSERVED" or observed.get("launchTimestamp")=="NOT_OBSERVED":
            return {"schema":SCHEMA,"status":"PROCESS_NOT_OBSERVABLE"}
        ref=uuid.uuid4().hex
        self.bindings[ref]={"hostRef":host_ref,"pid":process.pid,
                             "stamp":observed["launchTimestamp"]}
        return {"schema":SCHEMA,"status":"PROCESS_OBSERVED","bindingRef":ref,
                "processId":process.pid,"windowRef":None,"spawned":True}

    def activate(self,binding_ref,host_ref):
        b=self.bindings.get(binding_ref)
        if b is None or b["hostRef"]!=host_ref:
            return {"schema":SCHEMA,"status":"BINDING_NOT_FOUND","focused":False}
        observed=self._call("inspect",b["pid"])
        if observed.get("status")!="PROCESS_OBSERVED" or observed.get("launchTimestamp")!=b["stamp"]:
            return {"schema":SCHEMA,"status":"PROCESS_IDENTITY_CHANGED","focused":False}
        result=self._call("activate",b["pid"])
        return {"schema":SCHEMA,"status":result.get("status","NATIVE_PORT_FAILED"),
                "focused":result.get("status")=="PROCESS_FOCUSED"}

    def close_binding(self,binding_ref,host_ref):
        b=self.bindings.get(binding_ref)
        if b is None or b["hostRef"]!=host_ref:
            return {"schema":SCHEMA,"status":"BINDING_NOT_FOUND"}
        del self.bindings[binding_ref]
        return {"schema":SCHEMA,"status":"BINDING_CLOSED","processTerminated":False}
