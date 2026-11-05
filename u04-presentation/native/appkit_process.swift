import AppKit
import Foundation

// Native macOS AppKit process presentation observation: not an embed/window API.
func output(_ status: String, _ fields: [String: Any] = [:]) {
    var dict: [String: Any] = ["schema":"IWXA_U04_MACOS_APPKIT/1",
                              "status":status]
    for (key,value) in fields { dict[key] = value }
    if let bytes = try? JSONSerialization.data(withJSONObject:dict,options:[.sortedKeys]),
       let text = String(data:bytes,encoding:.utf8) {print(text)}
    else {print("{\"status\":\"SERIALIZATION_FAILED\"}")}
}
if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "lookup" {
    let bundleId = CommandLine.arguments[2]
    let candidates = NSRunningApplication.runningApplications(withBundleIdentifier:bundleId)
        .filter { !$0.isTerminated }
    if let active = candidates.first {
        output("PROCESS_OBSERVED",[
            "processId":Int(active.processIdentifier),
            "launchTimestamp":active.launchDate.map {
                String(format:"%.6f",$0.timeIntervalSince1970)
            } ?? "NOT_OBSERVED",
            "isFinishedLaunching":active.isFinishedLaunching,
            "isActive":active.isActive,
            "instanceCount":candidates.count
        ])
    } else { output("PROCESS_NOT_FOUND") }
    exit(0)
}
guard CommandLine.arguments.count == 3,
      ["inspect","activate","foreground"].contains(CommandLine.arguments[1]),
      let pidValue = Int32(CommandLine.arguments[2]), pidValue > 0
else {output("INVALID_REQUEST");exit(2)}
if CommandLine.arguments[1] == "foreground" {
    let foreground = NSWorkspace.shared.frontmostApplication?.processIdentifier
    output(foreground == pidValue ? "PROCESS_FRONTMOST" : "PROCESS_NOT_FRONTMOST",
           ["frontmostProcessId":foreground.map(Int.init) ?? -1])
    exit(0)
}
guard let running = NSRunningApplication(processIdentifier:pidValue),!running.isTerminated
else {output("PROCESS_NOT_FOUND");exit(0)}
let launch = running.launchDate?.timeIntervalSince1970
let fields: [String: Any] = [
    "processId": Int(pidValue),
    "launchTimestamp": launch.map {String(format:"%.6f",$0)} ?? "NOT_OBSERVED",
    "isActive":running.isActive,
    "isFinishedLaunching":running.isFinishedLaunching,
    "windowRef":NSNull(),
    "embeddedSupported":false,
    "linkedWindowSupported":false
]
if CommandLine.arguments[1] == "inspect" {
    output("PROCESS_OBSERVED",fields)
} else {
    _ = running.activate(options:[])
    Thread.sleep(forTimeInterval:0.2)
    let refreshed = NSRunningApplication(processIdentifier:pidValue)
    output(refreshed?.isActive == true ? "PROCESS_FOCUSED" : "PROCESS_FOCUS_DENIED",
           fields.merging(["focused":refreshed?.isActive == true],
                          uniquingKeysWith: {_,new in new}))
}
