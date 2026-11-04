// Shared WebView/Tauri binding. The product host provides its existing
// @tauri-apps/api/core invoke and registers the command on the native side.
// No Node APIs, Python interpreter, HTTP proxy or persistent daemon.
import {createHostAdapter} from "./host-port.mjs";

export function createTauriProgramPort(invoke, command="iwxa_program_connection") {
  if (typeof invoke !== "function" || command !== "iwxa_program_connection")
    throw Error("TAURI_PROGRAM_COMMAND_INVALID");
  return createHostAdapter(envelope => invoke(command, {envelope}));
}
