"""Native macOS program presentation via AppKit NSRunningApplication.

No Accessibility permission requests; no unsupported window references or embedding.
The native port must be host-provided/compiled from appkit_process.swift.
"""
from pathlib import Path
import os
import subprocess
import uuid
import time
import plistlib

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
        bundle=approved.get("bundle_path")
        bundle_id=approved.get("bundle_id")
        if bundle is not None:
            args=approved.get("args",[])
            path=Path(bundle) if isinstance(bundle,str) else Path(".")
            if not isinstance(bundle,str) or not path.is_absolute() or not path.is_dir() or (
                not isinstance(bundle_id,str) or not bundle_id or len(bundle_id)>256
            ) or not isinstance(args,list) or len(args)>64 or any(
                not isinstance(a,str) or "\\0" in a or len(a)>16384 for a in args
            ):
                return {"schema":SCHEMA,"status":"APP_BUNDLE_INVALID"}
            try:
                with open(path/"Contents"/"Info.plist","rb") as stream:
                    plist=plistlib.load(stream)
                executable=plist.get("CFBundleExecutable")
                if (plist.get("CFBundleIdentifier")!=bundle_id or
                    not isinstance(executable,str) or
                    not (path/"Contents"/"MacOS"/executable).is_file()):
                    return {"schema":SCHEMA,"status":"APP_BUNDLE_INVALID"}
                launched=self.run(["open","-n","-a",str(path),"--args",*args],
                                  capture_output=True,text=True,timeout=20,check=False)
                if launched.returncode!=0:
                    return {"schema":SCHEMA,"status":"LAUNCH_FAILED"}
            except (OSError,ValueError,subprocess.TimeoutExpired):
                return {"schema":SCHEMA,"status":"LAUNCH_FAILED"}
            observed=None
            for _ in range(60):
                found=self._call("lookup",bundle_id)
                if found.get("status")=="PROCESS_OBSERVED" and found.get("instanceCount")==1:
                    inspected=self._call("inspect",found.get("processId"))
                    if inspected.get("status")=="PROCESS_OBSERVED" and inspected.get("isFinishedLaunching"):
                        observed=inspected
                        break
                elif found.get("instanceCount",0)>1:
                    return {"schema":SCHEMA,"status":"PROCESS_IDENTITY_AMBIGUOUS"}
                time.sleep(.2)
            if not observed or observed.get("launchTimestamp") in (None,"NOT_OBSERVED"):
                return {"schema":SCHEMA,"status":"PROCESS_NOT_OBSERVABLE"}
            ref=uuid.uuid4().hex
            pid=observed["processId"]
            self.bindings[ref]={"hostRef":host_ref,"pid":pid,
                                "stamp":observed["launchTimestamp"]}
            return {"schema":SCHEMA,"status":"PROCESS_OBSERVED","bindingRef":ref,
                    "processId":pid,"processStamp":observed["launchTimestamp"],
                    "windowRef":None,"spawned":True}
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

    def reattach(self,app_ref,host_ref,pid,stamp):
        if not self._supported():
            return {"schema":SCHEMA,"status":"PLATFORM_UNSUPPORTED"}
        if type(pid) is not int or pid<=0 or not isinstance(stamp,str) or (
            stamp=="NOT_OBSERVED" or len(stamp)>64
        ):
            return {"schema":SCHEMA,"status":"PROCESS_IDENTITY_INVALID"}
        approved=self.resolver(app_ref,host_ref,
            {"operation":"reattach","pid":pid,"stamp":stamp})
        if not isinstance(approved,dict) or approved.get("approved") is not True or (
            approved.get("pid")!=pid or approved.get("stamp")!=stamp
        ):
            return {"schema":SCHEMA,"status":"HOST_APPROVAL_REQUIRED"}
        observed=self._call("inspect",pid)
        if observed.get("status")!="PROCESS_OBSERVED" or observed.get("launchTimestamp")!=stamp:
            return {"schema":SCHEMA,"status":"PROCESS_IDENTITY_CHANGED"}
        ref=uuid.uuid4().hex
        self.bindings[ref]={"hostRef":host_ref,"pid":pid,"stamp":stamp}
        return {"schema":SCHEMA,"status":"PROCESS_OBSERVED","bindingRef":ref,
                "processId":pid,"processStamp":stamp,"spawned":False,"windowRef":None}

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
