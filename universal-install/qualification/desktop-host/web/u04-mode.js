// Optional U04 presentation exercise inside the already installed Tauri host.
// Does not change the existing U01 program-invocation route.
(async () => {
  const native = window.__TAURI__?.core?.invoke;
  if (typeof native !== "function") return;
  let caps;
  try { caps = await native("iwxa_u04_presentation", {operation:"capabilities"}); }
  catch { return; }
  if (caps?.qualificationMode !== true) return;
  const old = document.getElementById("invoke");
  old.hidden = true;
  document.querySelector("h1").textContent = "IWXA External Application Bridge";
  document.querySelector("p").textContent =
    "Open the real external application, focus its existing window and detach without closing it.";
  const panel = document.createElement("div");
  panel.id = "u04-controls";
  panel.style.cssText = "position:absolute;left:32px;top:126px;display:flex;gap:18px";
  const ops = [
    ["open","Open external app","#234e8a"],
    ["activate","Focus existing","#ae701c"],
    ["closeBinding","Detach (keep app)","#6540a0"]
  ];
  const result = document.getElementById("result");
  result.className = "";
  result.textContent = "U04 ready — select Open external app";
  for (const [operation,label,color] of ops) {
    const button = document.createElement("button");
    button.id = "u04-" + operation;
    button.type = "button";
    button.textContent = label;
    button.style.cssText = "position:static;width:160px;height:56px;background:"+color+
      ";color:white;border:0;border-radius:8px;font-size:14px;cursor:pointer";
    button.addEventListener("click",async () => {
      button.disabled = true;
      try {
        const response = await native("iwxa_u04_presentation",{operation});
        result.className = response.status === "WINDOW_FOCUSED" ||
          response.status === "BINDING_CLOSED" ||
          response.status === "WINDOW_OBSERVED" ? "verified" : "";
        result.textContent = operation + ": " + response.status;
      } catch(err) {
        result.className = "failed";
        result.textContent = operation + ": " + String(err);
      } finally { button.disabled = false; }
    });
    panel.appendChild(button);
  }
  document.body.appendChild(panel);
})();
