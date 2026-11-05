import AppKit
import Foundation

// Real external AppKit window. It owns a user document; the U04 host never does.
guard CommandLine.arguments.count == 2 else { exit(2) }
let document = URL(fileURLWithPath:CommandLine.arguments[1])
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let window = NSWindow(contentRect:NSRect(x:80,y:80,width:540,height:360),
    styleMask:[.titled,.closable,.miniaturizable,.resizable],
    backing:.buffered,defer:false)
window.title = "IWXA U04 Independent macOS Editor"
let text = NSTextView(frame:NSRect(x:10,y:10,width:515,height:325))
text.isEditable = true
text.string = (try? String(contentsOf:document,encoding:.utf8)) ?? ""
window.contentView?.addSubview(text)
window.makeKeyAndOrderFront(nil)
app.activate(ignoringOtherApps:false)
app.run()
