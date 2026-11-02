import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent as ReactKeyEvent, MouseEvent, WheelEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  ArrowLeft, ArrowRight, Bookmark, BookOpen, Clock3, Globe2, Home, Plus,
  MoreHorizontal, RefreshCw, RotateCcw, Search, ShieldAlert, X
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
  granted_sites: string[];
  site_picker_available: boolean;
  debug_probe_available: boolean;
};
type SiteId = "learn" | "artbazi";
type SiteApproval = {site: SiteId; url: string};
type LeaveAction = { kind: "quit" | "restart" | "reset" } | { kind: "close_tab"; id: string };
const siteNames: Record<SiteId,string> = {learn: "Learn", artbazi: "ArtBazi"};
function previewSiteFor(url: string): SiteId | null {
  try {
    const origin = new URL(url).origin;
    if (origin === "https://learn.iwxa.ru") return "learn";
    if (origin === "https://artbazi.ru") return "artbazi";
  } catch { /* Native validation remains the authority for URL syntax. */ }
  return null;
}
type Action = { type: "boot" | "restart" | "capture" | "back" | "forward" | "reload" | "probe_workspace" }
  | { type: "new_tab" | "navigate"; url: string }
  | { type: "open_site"; site: SiteId; url?: string }
  | { type: "reset_sites" }
  | { type: "select_tab" | "close_tab"; id: string }
  | { type: "resize"; width: number; height: number }
  | { type: "click" | "move"; x: number; y: number }
  | { type: "wheel"; x: number; y: number; dx: number; dy: number }
  | { type: "key"; key: string; code: string; modifiers: number }
  | { type: "text"; text: string };

const blank: View = {
  active: null, tabs: [], frame: null, width: 960, height: 620, status: "Engine not started",
  granted_sites: [], site_picker_available: false, debug_probe_available: false
};
const bookmarksKey = "obscura-browser-lab.bookmarks";
const historyKey = "obscura-browser-lab.history";
type Visit = {url:string;title:string;visitedAt:number};
function storedHistory(): Visit[] {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(historyKey) || "[]");
    return Array.isArray(raw) ? raw.filter((x): x is Visit =>
      typeof x?.url === "string" && typeof x?.title === "string" &&
      typeof x?.visitedAt === "number" && x.url.length < 2048).slice(0,100) : [];
  } catch { return []; }
}
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
  return trimmed; // This preview has no general-purpose web search.
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
  const [siteApproval, setSiteApproval] = useState<SiteApproval | null>(null);
  const [leaveAction, setLeaveAction] = useState<LeaveAction | null>(null);
  const [exiting, setExiting] = useState(false);
  const [bookmarks, setBookmarks] = useState(stored);
  const [bookmarksOpen, setBookmarksOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [history, setHistory] = useState(storedHistory);
  const lastObservedVisit = useRef("");
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const pending = useRef(0);
  const lastPointerMove = useRef(0);
  const surface = useRef<HTMLDivElement>(null);
  const address = useRef<HTMLInputElement>(null);
  const activeTabButton = useRef<HTMLButtonElement>(null);
  const activeView = useRef(view);
  const composing = useRef(false);
  activeView.current = view;

  const issue = useCallback((action: Action) => {
    pending.current++;
    const work = queue.current.then(async () => {
      const next = await invoke<View>("command", { action });
      const previous = activeView.current;
      if (action.type !== "capture" || previous.active !== next.active ||
        previous.status !== next.status || JSON.stringify(previous.tabs) !== JSON.stringify(next.tabs)) {
        activeView.current = next;
        setView(next);
      }
      const tab = next.tabs.find(t => t.id === next.active);
      if (tab && tab.url !== "about:blank") {
        const visitKey = tab.id + ":" + tab.url;
        if (lastObservedVisit.current !== visitKey) {
          lastObservedVisit.current = visitKey;
          setHistory(items => [{url:tab.url,title:tab.title || tab.url,visitedAt:Date.now()},
            ...items.filter(item=>item.url!==tab.url)].slice(0,100));
        }
      }
      if (next.frame && action.type === "capture") setImage("data:image/png;base64," + next.frame);
      if (["select_tab", "new_tab", "close_tab", "navigate", "back", "forward", "reload", "restart", "open_site", "reset_sites"].includes(action.type)) setImage("");
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

  const performLeave = useCallback((action: LeaveAction) => {
    if (action.kind === "quit") {
      setExiting(true);
      // Never issue a synthetic save to the website: T2 owns its own Result
      // commits and only the site's own UI can confirm their completion.
      void getCurrentWindow().destroy().catch((reason: unknown) => {
        setExiting(false);
        setError("Could not close browser: " + String(reason));
      });
    } else if (action.kind === "restart") void issue({ type: "restart" });
    else if (action.kind === "reset") void issue({ type: "reset_sites" });
    else if (action.kind === "close_tab") void issue({ type: "close_tab", id: action.id });
  }, [issue]);

  const askBeforeLeaving = useCallback((action: LeaveAction) => {
    setToolsOpen(false);
    const current = activeView.current;
    // Keep the discriminated-union value narrowed outside the callback:
    // TypeScript does not preserve it inside Array.some's closure.
    const closingTabId = action.kind === "close_tab" ? action.id : null;
    const affected = closingTabId !== null
      ? current.tabs.some(tab => tab.id === closingTabId && tab.url !== "about:blank")
      : current.tabs.some(tab => tab.url !== "about:blank");
    // A fixture never claims product persistence and must keep its existing
    // developer shortcuts. Real sites are asked to save before losing state.
    if (current.site_picker_available && affected) {
      setLeaveAction(action);
    } else performLeave(action);
  }, [performLeave]);

  useEffect(() => {
    const window = getCurrentWindow();
    let unlisten: (() => void) | undefined;
    let disposed = false;
    void window.onCloseRequested(event => {
      // OS window close, not just the 'Finish work' menu. We cannot detect
      // dirty forms inside an arbitrary Obscura page: show an honest reminder
      // whenever a real-site tab exists, regardless of current active tab.
      if (activeView.current.tabs.some(tab => tab.url !== "about:blank")) {
        event.preventDefault();
        setSiteApproval(null);
        setToolsOpen(false);
        setLeaveAction({ kind: "quit" });
      }
    }).then(remove => {
      if (disposed) remove();
      else unlisten = remove;
    }).catch((reason: unknown) => setError("Close protection unavailable: " + String(reason)));
    return () => { disposed = true; unlisten?.(); };
  }, []);

  useEffect(() => { void issue({ type: "boot" }); }, [issue]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (pending.current === 0 && !document.hidden && activeView.current.active) void issue({ type: "capture" });
    }, 125);
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
      if (leaveAction) {
        if (e.key === "Escape") { e.preventDefault(); setLeaveAction(null); }
        return;
      }
      if (e.key === "Escape" && siteApproval) {
        e.preventDefault();
        setSiteApproval(null);
        return;
      }
      if (siteApproval) return;
      if (e.ctrlKey || e.metaKey) {
        if (e.altKey && e.shiftKey && e.key.toLowerCase() === "e") {
          e.preventDefault(); askBeforeLeaving({ kind: "restart" }); return;
        }
        if (e.altKey && e.shiftKey && e.key.toLowerCase() === "d" && activeView.current.debug_probe_available) {
          e.preventDefault(); void issue({type:"probe_workspace"}); return;
        }
        if (e.key.toLowerCase() === "l") { e.preventDefault(); address.current?.focus(); address.current?.select(); }
        if (e.key.toLowerCase() === "t") { e.preventDefault(); void issue({ type: "new_tab", url: "about:blank" }); }
        if (e.key.toLowerCase() === "w") {
          e.preventDefault(); const id = activeView.current.active;
          if (id) askBeforeLeaving({ kind: "close_tab", id });
          return;
        }
        if (e.key.toLowerCase() === "r") { e.preventDefault(); void issue({ type: "reload" }); }
      } else if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); void issue({ type: "back" }); }
      else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); void issue({ type: "forward" }); }
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [issue, askBeforeLeaving, siteApproval, leaveAction]);

  useEffect(() => { activeTabButton.current?.scrollIntoView({block: "nearest", inline: "nearest"}); }, [view.active]);
  useEffect(() => { localStorage.setItem(bookmarksKey, JSON.stringify(bookmarks)); }, [bookmarks]);
  useEffect(() => { localStorage.setItem(historyKey, JSON.stringify(history)); }, [history]);

  const active = view.tabs.find(t => t.id === view.active);
  const saved = Boolean(active && bookmarks.includes(active.url));

  function visit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const url = addressToUrl(input);
    if (view.site_picker_available && url !== "about:blank") {
      const site = previewSiteFor(url);
      if (!site) {
        setError("This preview accepts only HTTPS addresses on Learn or ArtBazi.");
      } else if (!view.granted_sites.includes(site)) {
        setSiteApproval({site, url});
        setError(null);
      } else {
        void issue({type:"navigate",url});
      }
    } else {
      void issue({type:"navigate",url});
    }
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
  function move(e: MouseEvent<HTMLDivElement>) {
    // Hover menus need real mouseMoved events inside Obscura, but not a
    // high-frequency CDP stream. Keep shell CPU and IPC bounded.
    const now = performance.now();
    if (now - lastPointerMove.current < 120 || pending.current > 1) return;
    const rect = e.currentTarget.getBoundingClientRect();
    lastPointerMove.current = now;
    void issue({ type: "move",
      x: Math.round((e.clientX - rect.left) * view.width / rect.width),
      y: Math.round((e.clientY - rect.top) * view.height / rect.height)
    });
  }
  function wheel(e: WheelEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    void issue({ type: "wheel", dx: e.deltaX, dy: e.deltaY,
      x: Math.round((e.clientX - rect.left) * view.width / rect.width),
      y: Math.round((e.clientY - rect.top) * view.height / rect.height)
    });
  }
  function keys(e: ReactKeyEvent<HTMLDivElement>) {
    if (composing.current || e.nativeEvent.isComposing) return;
    const modified = e.ctrlKey || e.metaKey;
    const lower = e.key.toLowerCase();
    // Chrome shortcuts belong to the shell, not the screenshot-backed page.
    // Everything else, including Ctrl+A for fields, is a webpage key.
    if ((modified && !e.altKey && ["l", "t", "w", "r"].includes(lower)) ||
        (e.altKey && !modified && ["ArrowLeft", "ArrowRight"].includes(e.key)) ||
        (modified && e.altKey && e.shiftKey && (lower === "e" || lower === "d"))) return;
    e.preventDefault();
    if (e.key.length === 1 && !modified && !e.altKey) {
      void issue({ type: "text", text: e.key });
    } else {
      void issue({ type: "key", key: e.key, code: e.code, modifiers:
        (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0)
      });
    }
  }
  return <div className="browser-shell">
    <header>
      <div className="tab-strip">
        <div role="tablist" aria-label="Tabs" className="tab-list">
          {view.tabs.map(tab => <div key={tab.id} className={"tab" + (tab.id === view.active ? " active" : "")}>
            <button className="tab-select" role="tab" aria-selected={tab.id === view.active}
              ref={tab.id === view.active ? activeTabButton : null}
              onClick={() => void issue({ type: "select_tab", id: tab.id })}>
              <Globe2 size={14}/><span>{tab.url === "about:blank" ? "New tab" : tab.title || tab.url}</span>
            </button>
            <IconButton label="Close tab" onClick={() => askBeforeLeaving({ kind: "close_tab", id: tab.id })}><X size={13}/></IconButton>
          </div>)}
        </div>
        <IconButton label="New tab (Ctrl+T)" onClick={() => void issue({ type: "new_tab", url: "about:blank" })}><Plus size={17}/></IconButton>
      </div>
      <div className="toolbar">
        <IconButton label="Back (Alt+Left)" disabled={!active?.back} onClick={() => void issue({ type: "back" })}><ArrowLeft size={18}/></IconButton>
        <IconButton label="Forward (Alt+Right)" disabled={!active?.forward} onClick={() => void issue({ type: "forward" })}><ArrowRight size={18}/></IconButton>
        <IconButton label="Reload (Ctrl+R)" disabled={!active} onClick={() => void issue({ type: "reload" })}><RefreshCw size={17}/></IconButton>
        <IconButton label="Home" disabled={!active || active.url === "about:blank"}
          onClick={() => void issue({ type: "navigate", url: "about:blank" })}><Home size={17}/></IconButton>
        <form className="omnibox" onSubmit={visit}>
          <ShieldAlert size={16} aria-hidden="true" className="origin-icon"/>
          <input aria-label="Web address" ref={address} placeholder="Enter an approved HTTPS address"
            value={input} onChange={e => setInput(e.target.value)} autoComplete="off" spellCheck={false}/>
          <button type="submit" title="Open address" className="go-button">Go</button>
        </form>
        <IconButton label={saved ? "Remove bookmark" : "Bookmark"} disabled={!active || active.url === "about:blank"}
          onClick={() => active && setBookmarks(prev => saved ? prev.filter(x => x !== active.url) : [active.url, ...prev].slice(0, 100))}>
          <Bookmark size={17} fill={saved ? "currentColor" : "none"}/></IconButton>
        <IconButton label="Bookmarks" onClick={() => {setHistoryOpen(false);setToolsOpen(false);setBookmarksOpen(x => !x);}}><BookOpen size={18}/></IconButton>
        <IconButton label="History" onClick={() => {setBookmarksOpen(false);setToolsOpen(false);setHistoryOpen(x => !x);}}><Clock3 size={18}/></IconButton>
        <div className="more-wrapper">
          <IconButton label="Browser options" onClick={() => {setBookmarksOpen(false);setHistoryOpen(false);setToolsOpen(x => !x);}}><MoreHorizontal size={20}/></IconButton>
          {toolsOpen && <div className="more-menu" role="menu" aria-label="Browser options">
            <div className="more-menu-title">Browser controls</div>
            <button role="menuitem" type="button" onClick={() => askBeforeLeaving({kind:"restart"})}>
              <RotateCcw size={16}/> Restart web engine
            </button>
            {view.granted_sites.length > 0 && <button role="menuitem" type="button"
              onClick={() => askBeforeLeaving({kind:"reset"})}>
              <ShieldAlert size={16}/> Forget site approvals and close tabs
            </button>}
            <button role="menuitem" type="button" onClick={() => askBeforeLeaving({kind:"quit"})}>
              <X size={16}/> Finish work / Close browser
            </button>
            <p>Save results in the website before leaving. Reopening tabs does not restore unfinished changes.</p>
          </div>}
        </div>
      </div>
    </header>
    <main>
      <div className="page" ref={surface}>
        {image && active && active.url !== "about:blank" ? <div className="interactive-frame" role="application" aria-label="Obscura captured webpage"
          tabIndex={0} onClick={point} onMouseMove={move} onWheel={wheel} onKeyDown={keys}
          onCompositionStart={() => { composing.current = true; }}
          onCompositionEnd={e => { composing.current = false; if (e.data) void issue({ type: "text", text: e.data }); }}>
          <img draggable={false} alt="Obscura webpage capture" src={image}/>
        </div> : active && active.url !== "about:blank" ? <section className="loading-page" aria-label="Loading website">
          <Globe2 size={29}/><strong>Opening approved website</strong>
          <p>{active.url}</p><small>Page content is still loading in Obscura.</small>
        </section> : <section className="start-page" aria-label="New tab">
          <div className="start-content">
            <div className="start-emblem" aria-hidden="true">i</div>
            <div className="start-eyebrow">IWXA BROWSER / OPEN PREVIEW</div>
            <h1>Where would you like to go?</h1>
            <p className="start-copy">Choose a trusted IWXA site to begin. General web access is not enabled in this preview.</p>
            <form className="start-search" onSubmit={visit}>
              <Search size={21} aria-hidden="true"/>
              <input aria-label="Web address" placeholder="Enter an approved HTTPS address"
                value={input} onChange={e => setInput(e.target.value)} autoComplete="off" spellCheck={false}/>
              <button type="submit">Open</button>
            </form>
            {view.site_picker_available && <div className="site-picker" aria-label="Available preview sites">
              <div className="site-picker-label">Available sites · choose to approve and open</div>
              <div className="site-choices">
                <button type="button" className="site-choice" onClick={() => void issue({type:"open_site",site:"learn"})}>
                  <strong>Learn</strong><span>Learning, practice and Workspace</span>
                  <small>learn.iwxa.ru {view.granted_sites.includes("learn") ? "· approved" : "· approve and open"}</small>
                </button>
                <button type="button" className="site-choice" onClick={() => void issue({type:"open_site",site:"artbazi"})}>
                  <strong>ArtBazi</strong><span>Marketing, sales and Workspace</span>
                  <small>artbazi.ru {view.granted_sites.includes("artbazi") ? "· approved" : "· approve and open"}</small>
                </button>
              </div>
            </div>}
            <div className="start-hint"><ShieldAlert size={15}/> Only exact approved HTTPS sites; this is not a secure general-purpose browser.</div>
            <div className="start-shortcuts"><span><kbd>Ctrl</kbd> + <kbd>L</kbd> Address</span><span><kbd>Ctrl</kbd> + <kbd>T</kbd> New tab</span></div>
          </div>
        </section>}
      </div>
      {bookmarksOpen && <aside className="drawer"><div className="drawer-title">Bookmarks
        <IconButton label="Close" onClick={() => setBookmarksOpen(false)}><X size={15}/></IconButton></div>
        {bookmarks.length === 0 ? <p>No bookmarks.</p> : bookmarks.map(url =>
          <button className="bookmark-entry" key={url} onClick={() => {void issue({type:"navigate",url});setBookmarksOpen(false);}}>{url}</button>)}
      </aside>}
      {historyOpen && <aside className="drawer"><div className="drawer-title">History
        <IconButton label="Close history" onClick={() => setHistoryOpen(false)}><X size={15}/></IconButton></div>
        {history.length === 0 ? <p>No visited pages yet.</p> : history.map(item =>
          <button className="bookmark-entry" key={item.url} title={item.url}
            onClick={() => {void issue({type:"navigate",url:item.url});setHistoryOpen(false);}}>
            <strong>{item.title}</strong><small>{item.url}</small>
          </button>)}
      </aside>}
      {leaveAction && <div className="approval-backdrop">
        <section className="approval-dialog finish-dialog" role="alertdialog" aria-modal="true"
          aria-labelledby="finish-title" aria-describedby="finish-message">
          <ShieldAlert size={23} aria-hidden="true"/>
          <h2 id="finish-title">Save your work before leaving</h2>
          <p id="finish-message">Save the result in Learn or ArtBazi and wait for the website's
            confirmation. IWXA Browser cannot save unfinished forms, drafts or actions for you.</p>
          <p className="finish-note">Restoring a tab only restores its address, not unsaved work.
            Unexpected shutdowns may bypass this reminder.</p>
          <div className="approval-actions">
            <button type="button" className="approval-cancel" autoFocus disabled={exiting}
              onClick={() => setLeaveAction(null)}>Return and save</button>
            <button type="button" className="approval-allow" disabled={exiting}
              onClick={() => {
                const selected = leaveAction;
                setLeaveAction(null);
                performLeave(selected);
              }}>
              {leaveAction.kind === "quit" ? "Close browser" :
                leaveAction.kind === "restart" ? "Restart without saving" :
                leaveAction.kind === "reset" ? "Clear tabs and approvals" : "Close tab"}
            </button>
          </div>
        </section>
      </div>}
      {siteApproval && <div className="approval-backdrop">
        <section className="approval-dialog" role="dialog" aria-modal="true"
          aria-labelledby="approval-title" aria-describedby="approval-message">
          <ShieldAlert size={23} aria-hidden="true"/>
          <h2 id="approval-title">Open {siteNames[siteApproval.site]}?</h2>
          <p id="approval-message">This preview opens only selected IWXA sites.
            Allow this exact HTTPS site for this browser profile?</p>
          <code>{siteApproval.url}</code>
          <div className="approval-actions">
            <button type="button" className="approval-cancel" autoFocus
              onClick={() => setSiteApproval(null)}>Cancel</button>
            <button type="button" className="approval-allow"
              onClick={() => {
                const choice=siteApproval;
                setSiteApproval(null);
                void issue({type:"open_site",site:choice.site,url:choice.url});
              }}>Allow and open</button>
          </div>
        </section>
      </div>}
      {error && <div className="error" role="alert"><ShieldAlert size={17}/><span>{error}</span>
        <IconButton label="Dismiss" onClick={() => setError(null)}><X size={15}/></IconButton></div>}
    </main>
    <footer className="statusbar">
      <span className={"engine-status" + ((view.status.startsWith("Page ready") || view.status.startsWith("Workspace ready")) ? " ready" : "")}>
        <span className="dot"/>{view.status}</span><span className="spacer"/>
      <span className="preview-note" title="Only the site can confirm that a Result was saved. Unfinished work is not restored.">
        <ShieldAlert size={13}/> Save results in the site before exit</span>
    </footer>
  </div>;
}
