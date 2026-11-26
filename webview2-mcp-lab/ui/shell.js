'use strict';
const toggle = document.getElementById('toggle');
let visible = true;
toggle.addEventListener('click', async () => {
  toggle.disabled = true;
  try {
    await window.__TAURI__.core.invoke('set_pane', {visible: !visible});
    visible = !visible;
    toggle.textContent = visible ? 'Hide browser pane' : 'Show browser pane';
    document.getElementById('status').textContent = visible ? 'Browser visible' : 'Browser hidden';
  } catch (error) {
    document.getElementById('status').textContent = 'ERROR: ' + String(error);
  } finally { toggle.disabled = false; }
});
