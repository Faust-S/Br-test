import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent as ReactKeyEvent, MouseEvent, WheelEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  ArrowLeft, ArrowRight, Bookmark, BookOpen, Globe2, Home, Plus,
  RefreshCw, Search, ShieldAlert, X
} from "lucide-react";

type Tab = {
  id: string;
  url: string;
  title: string;
  back: boolean;
  forward: boolean;
};
type View = {
  active: string | null;
  tabs: Tab[];
  frame: string | null;
  width: number;
  height: number;
  status: string;
};
type Action = { type: "boot" | "capture" | "back" | "forward" | "reload" }
  | { type: "new_tab" | "navigate"; url: string }
  | { type: "select_tab" | "close_tab"; id: string }
  | { type: "resize"; width: number; height: number }
  | { type: "click"; x: number; y: number }
  | { type: "wheel"; x: number; y: number; dx: number; dy: number }
  | { type: "key"; key: string; code: string; modifiers: number }
  | { type: "text"; text: string };

const blank: View = {
  active: null, tabs: [], frame: null, width: 960, height: 620, status: "Engine not started"
};
const bookmarksKey = "obscura-browser-lab.bookmarks";
function stored(): string[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(bookmarksKey) || "[]");
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string").slice(0, 100) : [];
  } catch { return []; }
}
function addressToUrl(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "about:blank";
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  if (/^[a-z0-9.-]+\.[a-z]{2,}(?::\d+)?(?:\/.*)?$/i.test(trimmed)) return "https://" + trimmed;
  return "https://www.mojeek.com/search?q=" + encodeURIComponent(trimmed);
}
function IconButton(props: {label: string; disabled?: boolean; onClick: () => void; children: React.ReactNode}) {
  return <button className="icon-btn" title={props.label} aria-label={props.label}
    disabled={props.disabled} onClick={props.onClick}>{props.children}</button>;
}

export default function App() {
  const [view, setView] = useState<View>(blank);
  const [image, setImage] = useState("");
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [bookmarks, setBookmarks] = useState(stored);
  const [bookmarksOpen, setBookmarksOpen] = useState(false);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const pending = useRef(0);
  const surface = useRef<HTMLDivElement>(null);
  const address = useRef<HTMLInputElement>(null);
  const activeView = useRef(view);
  const composing = useRef(false);
  activeView.current = view;

  const issue = useCallback((action: Action) => {
    pending.current++;
    const work = queue.current.then(async () => {
      const next = await invoke<View>("command", { action });
      setView(next);
      if (next.frame && action.type === "capture") setImage("data:image/png;base64," + next.frame);
      if (["select_tab", "new_tab", "close_tab", "navigate", "back", "forward", "reload"].includes(action.type)) setImage("");
      if (action.type !== "capture" && action.type !== "resize") setError(null);
      if (document.activeElement !== address.current) {
        const tab = next.tabs.find(t => t.id === next.active);
        if (tab) setInput(tab.url === "about:blank" ? "" : tab.url);
      }
      return next;
    }).catch((reason: unknown) => {
      setError(String(reason));
      return null;
    }).finally(() => { pending.current--; });
    queue.current = work;
    return work;
  }, []);

  useEffect(() => { void issue({ type: "boot" }); }, [issue]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (pending.current === 0 && !document.hidden && activeView.current.active) void issue({ type: "capture" });
    }, 750);
    return () => window.clearInterval(timer);
  }, [issue]);

  useEffect(() => {
    const node = surface.current;
    if (!node) return;
    let previous = "";
    const observer = new ResizeObserver(entries => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      const width = Math.round(rect.width), height = Math.round(rect.height);
      if (width < 160 || height < 160) return;
      const key = width + "x" + height;
      if (key !== previous) { previous = key; void issue({ type: "resize", width, height }); }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [issue]);

  useEffect(() => {
    const handle = (e: globalThis.KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey) {
        if (e.key.toLowerCase() === "l") { e.preventDefault(); address.current?.focus(); address.current?.select(); }
        if (e.key.toLowerCase() === "t") { e.preventDefault(); void issue({ type: "new_tab", url: "about:blank" }); }
        if (e.key.toLowerCase() === "w") { e.preventDefault(); const id = activeView.current.active; if (id) void issue({ type: "close_tab", id }); }
        if (e.key.toLowerCase() === "r") { e.preventDefault(); void issue({ type: "reload" }); }
      } else if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); void issue({ type: "back" }); }
      else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); void issue({ type: "forward" }); }
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [issue]);

  useEffect(() => { localStorage.setItem(bookmarksKey, JSON.stringify(bookmarks)); }, [bookmarks]);

  const active = view.tabs.find(t => t.id === view.active);
  const saved = Boolean(active && bookmarks.includes(active.url));

  function visit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    void issue({ type: "navigate", url: addressToUrl(input) });
    address.current?.blur();
  }
  function point(e: MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    void issue({ type: "click",
      x: Math.round((e.clientX - rect.left) * view.width / rect.width),
      y: Math.round((e.clientY - rect.top) * view.height / rect.height)
    });
    e.currentTarget.focus();
  }
  function wheel(e: WheelEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    void issue({ type: "wheel", dx: e.deltaX, dy: e.deltaY,
      x: Math.round((e.clientX - rect.left) * view.width / rect.width),
      y: Math.round((e.clientY - rect.top) * view.height / rect.height)
    });
  }
  function keys(e: ReactKeyEvent<HTMLDivElement>) {
    if (composing.current || e.nativeEvent.isComposing || e.ctrlKey || e.metaKey || e.key === "Tab") return;
    e.preventDefault();
    if (e.key.length === 1) void issue({ type: "text", text: e.key });
    else void issue({ type: "key", key: e.key, code: e.code, modifiers:
      (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0)
    });
  }
  return <div className="browser-shell">
    <header>
      <div className="tab-strip">
        <strong className="brand">Obscura <small>Browser Lab</small></strong>
        <div role="tablist" aria-label="Tabs" className="tab-list">
          {view.tabs.map(tab => <div key={tab.id} className={"tab" + (tab.id === view.active ? " active" : "")}>
            <button className="tab-select" role="tab" aria-selected={tab.id === view.active}
              onClick={() => void issue({ type: "select_tab", id: tab.id })}>
              <Globe2 size={14}/><span>{tab.title || "New tab"}</span>
            </button>
            <IconButton label="Close tab" onClick={() => void issue({ type: "close_tab", id: tab.id })}><X size={13}/></IconButton>
          </div>)}
        </div>
        <IconButton label="New tab (Ctrl+T)" onClick={() => void issue({ type: "new_tab", url: "about:blank" })}><Plus size={17}/></IconButton>
      </div>
      <div className="toolbar">
        <IconButton label="Back (Alt+Left)" disabled={!active?.back} onClick={() => void issue({ type: "back" })}><ArrowLeft size={18}/></IconButton>
        <IconButton label="Forward (Alt+Right)" disabled={!active?.forward} onClick={() => void issue({ type: "forward" })}><ArrowRight size={18}/></IconButton>
        <IconButton label="Reload (Ctrl+R)" disabled={!active} onClick={() => void issue({ type: "reload" })}><RefreshCw size={17}/></IconButton>
        <IconButton label="New blank tab" onClick={() => void issue({ type: "new_tab", url: "about:blank" })}><Home size={17}/></IconButton>
        <form className="omnibox" onSubmit={visit}>
          <Search size={16} aria-hidden="true"/>
          <input aria-label="Address or search" ref={address} placeholder="Address or search · allowlisted origins only"
            value={input} onChange={e => setInput(e.target.value)} autoComplete="off" spellCheck={false}/>
          <button type="submit" title="Go">Go</button>
        </form>
        <IconButton label={saved ? "Remove bookmark" : "Bookmark"} disabled={!active || active.url === "about:blank"}
          onClick={() => active && setBookmarks(prev => saved ? prev.filter(x => x !== active.url) : [active.url, ...prev].slice(0, 100))}>
          <Bookmark size={17} fill={saved ? "currentColor" : "none"}/></IconButton>
        <IconButton label="Bookmarks" onClick={() => setBookmarksOpen(x => !x)}><BookOpen size={18}/></IconButton>
      </div>
    </header>
    <main>
      <div className="page" ref={surface}>
        {image && active ? <div className="interactive-frame" role="application" aria-label="Obscura captured webpage"
          tabIndex={0} onClick={point} onWheel={wheel} onKeyDown={keys}
          onCompositionStart={() => { composing.current = true; }}
          onCompositionEnd={e => { composing.current = false; if (e.data) void issue({ type: "text", text: e.data }); }}>
          <img draggable={false} alt="Obscura webpage capture" src={image}/>
        </div> : <div className="empty">
          <div className="logo">◎</div>
          <h1>Open Browser Lab</h1>
          <p>Standalone public Obscura prototype. It renders webpages in Obscura, not inside the privileged Tauri WebView.</p>
          <p>Set the explicitly allowlisted origins and verified Obscura executable in the local environment before browsing.</p>
        </div>}
      </div>
      {bookmarksOpen && <aside className="drawer"><div className="drawer-title">Bookmarks
        <IconButton label="Close" onClick={() => setBookmarksOpen(false)}><X size={15}/></IconButton></div>
        {bookmarks.length === 0 ? <p>No bookmarks.</p> : bookmarks.map(url =>
          <button className="bookmark-entry" key={url} onClick={() => {void issue({type:"navigate",url});setBookmarksOpen(false);}}>{url}</button>)}
      </aside>}
      {error && <div className="error" role="alert"><ShieldAlert size={17}/><span>{error}</span>
        <IconButton label="Dismiss" onClick={() => setError(null)}><X size={15}/></IconButton></div>}
    </main>
    <footer><span className="dot"/> {view.status}<span className="spacer"/>
      <span>Experimental · external sites unqualified · IndexedDB restart HOLD · no downloads or protected writes</span>
    </footer>
  </div>;
}
