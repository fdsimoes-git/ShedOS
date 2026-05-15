// ShedOS web GUI — vanilla JS frontend.
// Tabs unified across chat (brain sessions) + render assets (image/pdf/web).
// WebSocket talks to /ws (proxied to brain RPC by web_server.py).

(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  const els = {
    tabsBar:     $("#tabs"),
    chat:        $("#chat"),
    input:       $("#input"),
    sendBtn:     $("#send-btn"),
    newTabBtn:   $("#new-tab-btn"),
    status:      $("#status"),
    statusText:  $("#status-text"),
    hint:        $("#hint"),
    main:        $("#main"),
  };

  let ws = null;
  let tabs = [];          // unified: [{id, type, title, sessionId?, url?}]
  let currentTabId = null;
  let activeToolBubbles = {}; // {tool_use_id: {row, sessionId}}
  let renderViewport = null;  // div for non-chat tabs (created on demand)
  // The session whose response is currently streaming. Used to route
  // incoming WS events to the originating chat tab even if the user
  // switches tabs mid-response.
  let inFlightSessionId = null;

  // ---------- Markdown (small subset) -----------------------------------

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({
      "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;",
    }[c]));
  }

  function renderMarkdown(src) {
    if (!src) return "";
    const codeBlocks = [];
    src = src.replace(/```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g, (m, lang, code) => {
      const i = codeBlocks.length;
      codeBlocks.push(`<pre><code class="lang-${escapeHtml(lang)}">${escapeHtml(code)}</code></pre>`);
      return ` CODE${i} `;
    });
    let html = escapeHtml(src);
    html = html.replace(/^###### (.*)$/gm, "<h6>$1</h6>");
    html = html.replace(/^##### (.*)$/gm, "<h5>$1</h5>");
    html = html.replace(/^#### (.*)$/gm, "<h4>$1</h4>");
    html = html.replace(/^### (.*)$/gm, "<h3>$1</h3>");
    html = html.replace(/^## (.*)$/gm, "<h2>$1</h2>");
    html = html.replace(/^# (.*)$/gm, "<h1>$1</h1>");
    html = html.replace(/^&gt; ?(.*)$/gm, "<blockquote>$1</blockquote>");
    html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/(^|\W)\*([^*\n]+)\*/g, "$1<em>$2</em>");
    html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    // Whitelist link schemes — assistant text is rendered with innerHTML,
    // so a `javascript:` URL in a markdown link would execute in the GUI's
    // local context (same origin as the API/WS endpoints).
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, text, raw) => {
      // text and raw are already HTML-escaped because escapeHtml was applied
      // on line 48 before this replace runs. Re-escaping here would corrupt
      // URLs containing `&` (becomes `&amp;amp;`). Just validate the scheme.
      const safe = /^(https?:\/\/|mailto:|\/render\/|\/static\/|#)/i.test(raw.trim());
      if (!safe) return m;  // render the markdown source verbatim (already escaped)
      return `<a href="${raw}" target="_blank" rel="noopener noreferrer">${text}</a>`;
    });
    html = html.replace(/(^|\n)((?:[*\-] .*(?:\n|$))+)/g, (m, lead, block) => {
      const items = block.trim().split(/\n/).map(l => `<li>${l.replace(/^[*\-] /, "")}</li>`).join("");
      return `${lead}<ul>${items}</ul>`;
    });
    html = html.replace(/(^|\n)((?:\d+\. .*(?:\n|$))+)/g, (m, lead, block) => {
      const items = block.trim().split(/\n/).map(l => `<li>${l.replace(/^\d+\. /, "")}</li>`).join("");
      return `${lead}<ol>${items}</ol>`;
    });
    html = html.split(/\n{2,}/).map(chunk => {
      const t = chunk.trim();
      if (!t) return "";
      if (/^<(h\d|ul|ol|pre|blockquote|p)/.test(t)) return t;
      return `<p>${t.replace(/\n/g, "<br>")}</p>`;
    }).join("\n");
    html = html.replace(/ CODE(\d+) /g, (_, i) => codeBlocks[parseInt(i, 10)]);
    return html;
  }

  // ---------- Status ----------------------------------------------------

  function setStatus(state, text) {
    els.status.classList.remove("online", "error");
    if (state) els.status.classList.add(state);
    els.statusText.textContent = text;
  }

  // ---------- Tabs ------------------------------------------------------

  const TAB_ICONS = { chat: "◉", image: "🖼", pdf: "📄", web: "🌐" };

  function renderTabsBar() {
    els.tabsBar.innerHTML = "";
    tabs.forEach(t => {
      const tab = document.createElement("div");
      tab.className = "tab" + (t.id === currentTabId ? " active" : "") +
                       ` tab-${t.type}`;
      tab.dataset.id = t.id;
      tab.innerHTML = `
        <span class="icon">${TAB_ICONS[t.type] || "•"}</span>
        <span class="title">${escapeHtml(t.title || "untitled")}</span>
        <span class="close" title="Close tab">✕</span>
      `;
      tab.addEventListener("click", (e) => {
        if (e.target.classList.contains("close")) {
          e.stopPropagation();
          closeTab(t.id);
        } else {
          switchTo(t.id);
        }
      });
      els.tabsBar.appendChild(tab);
    });
  }

  function currentTab() {
    return tabs.find(t => t.id === currentTabId);
  }

  // ---------- Layout switching (chat vs render) -------------------------

  function showChatLayout() {
    els.chat.style.display = "";
    if (renderViewport) renderViewport.remove();
    renderViewport = null;
    els.main.classList.remove("render-mode");
  }

  function showRenderLayout(tab) {
    els.chat.style.display = "none";
    if (renderViewport) renderViewport.remove();
    renderViewport = document.createElement("div");
    renderViewport.className = "render-viewport";
    if (tab.type === "image") {
      renderViewport.innerHTML = `
        <div class="render-controls">
          <button class="btn-icon" data-zoom="in"  title="Zoom in">＋</button>
          <button class="btn-icon" data-zoom="out" title="Zoom out">－</button>
          <button class="btn-icon" data-zoom="fit" title="Fit">⤢</button>
          <span class="render-title">${escapeHtml(tab.title)}</span>
        </div>
        <div class="render-image-stage">
          <img src="${tab.url}" alt="${escapeHtml(tab.title)}">
        </div>
      `;
      let zoom = 1;
      const img = renderViewport.querySelector("img");
      const apply = () => { img.style.transform = `scale(${zoom})`; };
      renderViewport.addEventListener("click", (e) => {
        const z = e.target.dataset.zoom;
        if (z === "in") { zoom = Math.min(zoom * 1.2, 8); apply(); }
        else if (z === "out") { zoom = Math.max(zoom / 1.2, 0.1); apply(); }
        else if (z === "fit") { zoom = 1; apply(); }
      });
    } else if (tab.type === "pdf") {
      renderViewport.innerHTML = `
        <div class="render-controls">
          <span class="render-title">📄 ${escapeHtml(tab.title)}</span>
          <a class="btn-icon" href="${tab.url}" download title="Download">⬇</a>
        </div>
        <iframe src="${tab.url}" class="render-frame"></iframe>
      `;
    } else if (tab.type === "web") {
      renderViewport.innerHTML = `
        <div class="render-controls">
          <span class="render-title">🌐 ${escapeHtml(tab.title)}</span>
          <a class="btn-icon" href="${tab.url}" target="_blank" title="Open in new window">⇗</a>
        </div>
        <iframe src="${tab.url}" class="render-frame" sandbox="allow-scripts allow-same-origin allow-popups allow-forms"></iframe>
      `;
    }
    els.main.appendChild(renderViewport);
    els.main.classList.add("render-mode");
  }

  // ---------- Chat ------------------------------------------------------

  function clearChat() {
    els.chat.innerHTML = "";
    activeToolBubbles = {};
  }

  function renderEmpty(title) {
    els.chat.innerHTML = `
      <div class="empty">
        <div class="big">◆</div>
        <h2>${escapeHtml(title || "ShedOS")}</h2>
        <p>Type a message to start. Try "render an image of a corgi", or
           "open the wikipedia page about Linux".</p>
      </div>
    `;
  }

  function chatRoot(sessionId) {
    // Returns the chat DOM element to append messages to. Falls back to
    // the currently-visible chat container if no specific session is given.
    if (!sessionId) return els.chat;
    // Hidden chat panels could live elsewhere, but our current layout
    // only has one chat element. Detached-render is fine because when
    // the user switches back to the originating chat tab, loadChatHistory
    // re-fetches from the persistent backend and replays everything.
    return els.chat;
  }

  function bubble(role, opts = {}) {
    const row = document.createElement("div");
    row.className = `bubble-row ${role}`;
    if (opts.toolId) row.dataset.toolId = opts.toolId;
    if (opts.sessionId) row.dataset.sessionId = opts.sessionId;
    const avatar = document.createElement("div");
    avatar.className = `avatar ${role === "claude" ? "claude" : role === "tool" ? "tool" : "user"}`;
    avatar.textContent = role === "claude" ? "✦" : role === "tool" ? "⚙" : "Y";
    const body = document.createElement("div");
    body.className = "bubble" + (opts.markdown ? " markdown" : "");
    row.appendChild(avatar);
    row.appendChild(body);
    // Only append to the visible chat if the event belongs to the active
    // chat tab. Otherwise the message is persisted server-side and will
    // appear when the user switches to that chat (via history reload).
    const t = currentTab();
    const eventBelongsToCurrent =
      !opts.sessionId ||
      (t && t.type === "chat" && t.sessionId === opts.sessionId);
    if (eventBelongsToCurrent) {
      els.chat.appendChild(row);
      els.chat.scrollTop = els.chat.scrollHeight;
    }
    return { row, body };
  }

  function addUser(text, sessionId) {
    const { body } = bubble("user", { sessionId });
    body.innerHTML = `<div class="who">You</div>${escapeHtml(text).replace(/\n/g, "<br>")}`;
  }

  function addClaude(text, sessionId) {
    const { body } = bubble("claude", { markdown: true, sessionId });
    body.innerHTML = `<div class="who">Claude</div>${renderMarkdown(text)}`;
  }

  function addTool(name, summary, toolId, sessionId) {
    const { row, body } = bubble("tool", { toolId, sessionId });
    body.innerHTML = `
      <div class="tool-head">
        <span class="badge">running</span>
        <span class="spinner"></span>
        <span>${escapeHtml(name)}</span>
        <span class="tool-cmd">${escapeHtml(summary)}</span>
      </div>
      <div class="tool-output">…</div>
    `;
    activeToolBubbles[toolId] = { row, sessionId };
  }

  function setToolResult(toolId, output, ok) {
    const entry = activeToolBubbles[toolId];
    if (!entry) return;
    const row = entry.row;
    row.classList.add(ok ? "ok" : "err");
    const head = row.querySelector(".tool-head");
    if (head) {
      const badge = head.querySelector(".badge");
      const spinner = head.querySelector(".spinner");
      if (badge) badge.textContent = ok ? "ok" : "error";
      if (spinner) spinner.remove();
    }
    let txt = "";
    if (output && typeof output === "object") {
      if ("render" in output) {
        // Render envelope from a render_* tool — open a new tab.
        const r = output.render;
        addRenderTab(r);
        txt = `→ opened ${r.type} tab "${r.title}"`;
      } else if ("stdout" in output) {
        txt = output.stdout || "";
        if (output.stderr) txt += `\n[stderr]\n${output.stderr}`;
      } else if ("error" in output) {
        txt = `error: ${output.error}`;
      } else {
        txt = JSON.stringify(output, null, 2);
      }
    } else {
      txt = String(output);
    }
    if (txt.length > 4000) txt = txt.slice(0, 4000) + "\n... (truncated)";
    const out = row.querySelector(".tool-output");
    if (out) out.textContent = txt.trimEnd() || "(no output)";
    delete activeToolBubbles[toolId];
  }

  function addError(msg) {
    const { body } = bubble("error");
    body.innerHTML = `<div class="who">Error</div>${escapeHtml(msg)}`;
  }

  // ---------- Tab management --------------------------------------------

  function addRenderTab(render) {
    const id = `render-${render.id}`;
    if (tabs.find(t => t.id === id)) {
      // Tab already exists → just focus it
      switchTo(id);
      return;
    }
    tabs.push({
      id,
      type: render.type,
      title: render.title || render.type,
      url: render.url,
    });
    renderTabsBar();
    switchTo(id);
  }

  function closeTab(id) {
    const t = tabs.find(t => t.id === id);
    if (!t) return;
    const chatTabs = tabs.filter(t => t.type === "chat");
    if (t.type === "chat" && chatTabs.length <= 1) {
      // keep at least one chat tab
      return;
    }
    if (t.type === "chat") {
      // delete the brain session too
      apiDelete(`/api/sessions/${t.sessionId}`).catch(() => {});
    }
    tabs = tabs.filter(t => t.id !== id);
    if (currentTabId === id) {
      const fallback = tabs.find(t => t.type === "chat") || tabs[0];
      switchTo(fallback ? fallback.id : null);
    } else {
      renderTabsBar();
    }
  }

  function switchTo(id) {
    currentTabId = id;
    renderTabsBar();
    const t = currentTab();
    if (!t) {
      clearChat();
      renderEmpty("ShedOS");
      showChatLayout();
      return;
    }
    if (t.type === "chat") {
      showChatLayout();
      loadChatHistory(t);
    } else {
      showRenderLayout(t);
    }
  }

  async function loadChatHistory(tab) {
    clearChat();
    try {
      const data = await apiGet(`/api/sessions/${tab.sessionId}/messages`);
      const msgs = data.messages || [];
      if (!msgs.length) {
        renderEmpty(tab.title);
        return;
      }
      msgs.forEach(m => {
        if (m.role === "user" && typeof m.content === "string") {
          addUser(m.content);
        } else if (m.role === "assistant" && Array.isArray(m.content)) {
          m.content.forEach(b => {
            if (b.type === "text") addClaude(b.text || "");
            else if (b.type === "tool_use") {
              const id = b.id || `hist-${Math.random()}`;
              addTool(b.name || "?",
                      JSON.stringify(b.input || {}).slice(0, 60), id);
              setToolResult(id, { info: "(history)" }, true);
            }
          });
        }
      });
    } catch (e) {
      addError(`load history: ${e.message}`);
    }
  }

  // ---------- API + WS --------------------------------------------------

  async function apiGet(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }
  async function apiPost(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }
  async function apiDelete(path) {
    const r = await fetch(path, { method: "DELETE" });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.addEventListener("open", () => setStatus("online", "connected"));
    ws.addEventListener("close", () => {
      setStatus("error", "disconnected — retrying");
      setTimeout(connectWS, 2000);
    });
    ws.addEventListener("error", () => setStatus("error", "ws error"));
    ws.addEventListener("message", (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      handleServerMessage(msg);
    });
  }

  function handleServerMessage(msg) {
    const ev = msg.event;
    // Events flow over a single WS but belong to the inFlightSessionId
    // (set when we send). This way tab-switching mid-response doesn't
    // misroute the response into a different chat tab's DOM.
    const sid = inFlightSessionId;
    switch (ev) {
      case "user_msg": break;
      case "assistant_text": addClaude(msg.chunk || "", sid); break;
      case "tool_use":
        addTool(msg.name || "?", msg.input_summary || "", msg.id, sid);
        break;
      case "tool_result": {
        const out = msg.output || {};
        const ok = !(typeof out === "object" && "error" in out);
        setToolResult(msg.id, out, ok);
        break;
      }
      case "error": addError(msg.msg || "unknown error"); break;
      case "end_turn":
      case "_stream_done":
        inFlightSessionId = null;
        els.input.disabled = false;
        els.sendBtn.disabled = false;
        if (currentTab() && currentTab().type === "chat") els.input.focus();
        break;
    }
  }

  function sendMessage(text) {
    if (!ws || ws.readyState !== 1) {
      addError("not connected to server");
      return;
    }
    const t = currentTab();
    let chatTab = t && t.type === "chat" ? t : tabs.find(x => x.type === "chat");
    if (!chatTab) { addError("no active chat tab"); return; }
    if (chatTab.id !== currentTabId) switchTo(chatTab.id);
    inFlightSessionId = chatTab.sessionId;
    addUser(text, chatTab.sessionId);
    els.input.disabled = true;
    els.sendBtn.disabled = true;
    ws.send(JSON.stringify({ type: "send", session_id: chatTab.sessionId, text }));
  }

  // ---------- Session bootstrapping -------------------------------------

  async function refreshSessions() {
    try {
      const data = await apiGet("/api/sessions");
      const fetched = data.sessions || [];
      // Replace chat tabs with fresh data, keep render tabs as-is
      const existingRender = tabs.filter(t => t.type !== "chat");
      tabs = fetched.map(s => ({
        id: `chat-${s.id}`,
        type: "chat",
        title: s.title,
        sessionId: s.id,
      })).concat(existingRender);
    } catch {
      tabs = tabs.filter(t => t.type !== "chat");
    }
    renderTabsBar();
  }

  async function newChatTab() {
    try {
      const info = await apiPost("/api/sessions", { title: "New chat" });
      await refreshSessions();
      switchTo(`chat-${info.id}`);
    } catch (e) {
      addError(`new tab: ${e.message}`);
    }
  }

  // ---------- Wire up ---------------------------------------------------

  els.sendBtn.addEventListener("click", () => {
    const t = els.input.value.trim();
    if (!t) return;
    els.input.value = "";
    autoResize();
    sendMessage(t);
  });
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      els.sendBtn.click();
    } else if ((e.metaKey || e.ctrlKey) && e.key === "t") {
      e.preventDefault(); newChatTab();
    } else if ((e.metaKey || e.ctrlKey) && e.key === "w") {
      e.preventDefault(); closeTab(currentTabId);
    }
  });
  els.input.addEventListener("input", autoResize);
  function autoResize() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 240) + "px";
  }
  els.newTabBtn.addEventListener("click", newChatTab);

  els.hint.textContent = "Enter ↵ send · Shift+↵ newline · ⌘T new tab · ⌘W close tab";

  // Init
  setStatus(null, "connecting…");
  (async () => {
    await refreshSessions();
    if (!tabs.find(t => t.type === "chat")) {
      const info = await apiPost("/api/sessions", { title: "New chat" });
      await refreshSessions();
      switchTo(`chat-${info.id}`);
    } else {
      switchTo(tabs[0].id);
    }
    connectWS();
  })().catch(e => {
    addError(`init: ${e.message}`);
    setStatus("error", "init failed");
  });
})();
