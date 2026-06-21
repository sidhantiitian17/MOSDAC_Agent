// static/graph-rag-chat-widget.js
// Generic Graph RAG chat widget — domain-agnostic, embeddable via one <script> tag.
// Branding, backend URL and the SSO token source all come from runtime config, so
// the SAME file serves any portal. Nothing is hardcoded.
//
//   <script>
//     window.GRAPH_RAG_CHAT_CONFIG = {
//       apiBase:  '/chatapi',                      // backend prefix (per site)
//       botTitle: 'MOSDAC BOT',                    // header / sidebar title
//       logoUrl:  '/sites/default/files/isro.png', // logo URL
//       greeting: "Hey User, what's on your mind today?",
//       getToken: () => document.querySelector('meta[name=kc-token]')?.content || '',
//     };
//   </script>
//   <script src="/static/graph-rag-chat-widget.js"></script>
//
// Auth model:
//   * getToken() returns a Keycloak access token  → Authorization: Bearer <jwt>,
//     the user gets a persisted history sidebar.
//   * no token → anonymous, ephemeral chat, sidebar hidden (nothing persisted).

(function () {
  'use strict';

  // ── Defaults (light theme to match the MOSDAC reference design) ────────────
  const DEFAULTS = {
    apiBase:          '/chatapi',
    botTitle:         'MOSDAC BOT',
    logoUrl:          '',
    greeting:         "Hey User, what's on your mind today?",
    suggestions:      ['How can you help me browse?', 'What can you do?', 'Explain a topic'],
    // Auth
    getToken:         null,          // () => string | Promise<string>
    token:            '',            // static token alternative
    authMode:         'token',       // 'token' | 'none'
    sidebarEnabled:   true,
    // Colours
    accent:           '#1565c0',
    accentHover:      '#0d47a1',
    panelBg:          '#ffffff',
    headerBg:         '#ffffff',
    sidebarBg:        '#f7f8fa',
    msgBotBg:         '#f1f3f8',
    msgUserBg:        '#1565c0',
    textColor:        '#16203a',
    mutedColor:       '#6b7280',
    borderColor:      '#e5e7eb',
    placeholderColor: '#9aa0b4',
    // Layout
    elementPrefix:    'grag',
    panelWidth:       460,
    enableScreenshot: true,
    fetchRemoteConfig: true,
  };

  const userCfg = window.GRAPH_RAG_CHAT_CONFIG || {};
  const cfg = Object.assign({}, DEFAULTS, userCfg);
  // Back-compat aliases (older config used title/botLogo).
  if (!userCfg.botTitle && userCfg.title) cfg.botTitle = userCfg.title;
  if (!userCfg.logoUrl && userCfg.botLogo) cfg.logoUrl = userCfg.botLogo;

  const P = cfg.elementPrefix;
  const SESSION_KEY = P + '_chat_sid';
  // UUID v4 (the backend validates session_id as a UUID).
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const SESSION_ID = (() => {
    let id = sessionStorage.getItem(SESSION_KEY);
    // The backend validates session_id as a UUID — regenerate any legacy/invalid value.
    if (!id || !UUID_RE.test(id)) {
      id = uuid();
      sessionStorage.setItem(SESSION_KEY, id);
    }
    return id;
  })();

  const ID = {
    toggle:     `${P}-chat-toggle`,
    panel:      `${P}-chat-panel`,
    header:     `${P}-chat-header`,
    hamburger:  `${P}-chat-hamburger`,
    close:      `${P}-chat-close`,
    body:       `${P}-chat-body`,
    sidebar:    `${P}-chat-sidebar`,
    sidebarList:`${P}-chat-sidebar-list`,
    newChat:    `${P}-chat-new`,
    chatArea:   `${P}-chat-area`,
    messages:   `${P}-chat-messages`,
    empty:      `${P}-chat-empty`,
    input:      `${P}-chat-input`,
    inputRow:   `${P}-chat-input-row`,
    sendBtn:    `${P}-btn-send`,
    ssBtn:      `${P}-btn-screenshot`,
    preview:    `${P}-attach-preview`,
    previewImg: `${P}-attach-img`,
    removeBtn:  `${P}-remove-attach`,
  };
  const CLS = {
    msg: `${P}-msg`, user: `${P}-msg-user`, bot: `${P}-msg-bot`,
    error: `${P}-msg-error`, typing: `${P}-typing`, thumb: `${P}-thumb`,
    iconBtn: `${P}-icon-btn`, convItem: `${P}-conv-item`,
    convDel: `${P}-conv-del`, chip: `${P}-chip`,
  };

  // ── State ──────────────────────────────────────────────────────────────────
  const state = {
    authed: false,
    token: '',
    conversations: [],
    activeConversationId: null,
    sidebarOpen: false,
    isWaiting: false,
    attachedScreenshot: null,
  };

  // ── Styles ──────────────────────────────────────────────────────────────────
  function injectCSS() {
    const style = document.createElement('style');
    style.textContent = `
      #${ID.toggle} {
        position: fixed; bottom: 28px; right: 28px; z-index: 9998;
        width: 56px; height: 56px; border-radius: 50%;
        background: ${cfg.accent}; border: none; cursor: pointer;
        box-shadow: 0 6px 18px rgba(0,0,0,0.28);
        display: flex; align-items: center; justify-content: center;
        transition: background .2s, transform .15s;
      }
      #${ID.toggle}:hover { background: ${cfg.accentHover}; transform: scale(1.05); }
      #${ID.toggle} svg { width: 28px; height: 28px; fill: #fff; }

      #${ID.panel} {
        position: fixed; top: 0; right: -${cfg.panelWidth + 30}px;
        width: ${cfg.panelWidth}px; max-width: 100vw; height: 100vh;
        background: ${cfg.panelBg}; color: ${cfg.textColor};
        box-shadow: -6px 0 28px rgba(0,0,0,0.18);
        display: flex; flex-direction: column; z-index: 9999;
        transition: right .3s ease;
        font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      }
      #${ID.panel}.open { right: 0; }

      #${ID.header} {
        background: ${cfg.headerBg}; padding: 12px 14px;
        display: flex; align-items: center; gap: 10px;
        border-bottom: 1px solid ${cfg.borderColor};
      }
      #${ID.header} .${P}-logo { width: 30px; height: 30px; border-radius: 6px; object-fit: contain; }
      #${ID.header} .${P}-title { font-weight: 700; font-size: 16px; letter-spacing: .3px; flex: 1; }
      .${P}-hdr-btn {
        background: none; border: none; color: ${cfg.textColor}; cursor: pointer;
        padding: 6px; border-radius: 8px; line-height: 0; opacity: .8;
      }
      .${P}-hdr-btn:hover { background: ${cfg.msgBotBg}; opacity: 1; }
      .${P}-hdr-btn svg { width: 20px; height: 20px; fill: currentColor; }

      #${ID.body} { flex: 1; position: relative; display: flex; min-height: 0; }

      #${ID.sidebar} {
        position: absolute; top: 0; left: 0; bottom: 0; width: 78%;
        background: ${cfg.sidebarBg}; border-right: 1px solid ${cfg.borderColor};
        display: flex; flex-direction: column; z-index: 5;
        transform: translateX(-101%); transition: transform .25s ease;
        box-shadow: 2px 0 12px rgba(0,0,0,0.08);
      }
      #${ID.sidebar}.open { transform: translateX(0); }
      #${ID.newChat} {
        margin: 12px; padding: 10px 12px; border: 1px solid ${cfg.borderColor};
        background: ${cfg.panelBg}; color: ${cfg.textColor}; border-radius: 10px;
        cursor: pointer; font-size: 14px; display: flex; align-items: center; gap: 8px;
      }
      #${ID.newChat}:hover { border-color: ${cfg.accent}; color: ${cfg.accent}; }
      #${ID.newChat} svg { width: 16px; height: 16px; fill: currentColor; }
      #${ID.sidebarList} { list-style: none; margin: 0; padding: 0 8px 12px; overflow-y: auto; flex: 1; }
      .${CLS.convItem} {
        display: flex; align-items: center; gap: 6px; padding: 9px 10px; margin: 2px 0;
        border-radius: 8px; cursor: pointer; font-size: 13.5px; color: ${cfg.textColor};
      }
      .${CLS.convItem}:hover { background: ${cfg.msgBotBg}; }
      .${CLS.convItem}.active { background: ${cfg.msgBotBg}; font-weight: 600; }
      .${CLS.convItem} .${P}-conv-title {
        flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      .${CLS.convDel} {
        background: none; border: none; color: ${cfg.mutedColor}; cursor: pointer;
        font-size: 15px; line-height: 1; padding: 2px 4px; border-radius: 6px; opacity: 0;
      }
      .${CLS.convItem}:hover .${CLS.convDel} { opacity: 1; }
      .${CLS.convDel}:hover { color: #d32f2f; background: rgba(211,47,47,.1); }

      #${ID.chatArea} { flex: 1; display: flex; flex-direction: column; min-width: 0; }
      #${ID.messages} {
        flex: 1; overflow-y: auto; padding: 16px;
        display: flex; flex-direction: column; gap: 12px;
      }
      #${ID.empty} {
        margin: auto; text-align: left; padding: 20px; width: 100%;
      }
      #${ID.empty} h2 { font-size: 26px; font-weight: 800; line-height: 1.2; margin: 0 0 18px; color: ${cfg.textColor}; }
      #${ID.empty} .${P}-chips { display: flex; flex-wrap: wrap; gap: 10px; }
      .${CLS.chip} {
        border: 1px solid ${cfg.borderColor}; background: ${cfg.panelBg};
        color: ${cfg.textColor}; border-radius: 18px; padding: 9px 16px;
        font-size: 13.5px; cursor: pointer;
      }
      .${CLS.chip}:hover { border-color: ${cfg.accent}; color: ${cfg.accent}; }

      .${CLS.msg} {
        max-width: 88%; padding: 11px 14px; border-radius: 14px;
        font-size: 14px; line-height: 1.55; word-break: break-word;
      }
      .${CLS.user} { align-self: flex-end; background: ${cfg.msgUserBg}; color: #fff; border-bottom-right-radius: 4px; }
      .${CLS.bot}  { align-self: flex-start; background: ${cfg.msgBotBg}; color: ${cfg.textColor}; border-bottom-left-radius: 4px; white-space: pre-wrap; }
      .${CLS.error} { background: #fdecea; color: #b71c1c; align-self: flex-start; }
      .${CLS.thumb} { max-width: 100%; border-radius: 8px; margin-top: 6px; display: block; border: 1px solid ${cfg.borderColor}; }
      .${CLS.typing} { color: ${cfg.mutedColor}; font-style: italic; }

      #${ID.preview} {
        margin: 0 14px; padding: 8px 10px; background: ${cfg.msgBotBg};
        border-radius: 8px; font-size: 12px; display: none; align-items: center; gap: 8px;
      }
      #${ID.preview} img { height: 44px; border-radius: 4px; }
      #${ID.removeBtn} { background: none; border: none; color: #d32f2f; font-size: 16px; cursor: pointer; margin-left: auto; }

      #${ID.inputRow} {
        padding: 12px; border-top: 1px solid ${cfg.borderColor};
        display: flex; gap: 8px; align-items: flex-end;
      }
      #${ID.input} {
        flex: 1; background: #fff; border: 1px solid ${cfg.borderColor};
        color: ${cfg.textColor}; border-radius: 22px; padding: 11px 16px;
        font-size: 14px; resize: none; outline: none; min-height: 22px; max-height: 120px; overflow-y: auto;
      }
      #${ID.input}:focus { border-color: ${cfg.accent}; }
      #${ID.input}::placeholder { color: ${cfg.placeholderColor}; }
      .${CLS.iconBtn} {
        background: ${cfg.accent}; border: none; border-radius: 50%;
        width: 42px; height: 42px; cursor: pointer; flex-shrink: 0;
        display: flex; align-items: center; justify-content: center; color: #fff; transition: background .2s;
      }
      .${CLS.iconBtn}:hover { background: ${cfg.accentHover}; }
      .${CLS.iconBtn} svg { width: 18px; height: 18px; fill: currentColor; }
      .${CLS.iconBtn}:disabled { opacity: .4; cursor: not-allowed; }
      .${P}-ghost-btn { background: ${cfg.msgBotBg}; color: ${cfg.textColor}; }
      .${P}-ghost-btn:hover { background: ${cfg.borderColor}; }
    `;
    document.head.appendChild(style);
  }

  function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function buildDOM() {
    const logoHTML = cfg.logoUrl
      ? `<img class="${P}-logo" src="${escapeHTML(cfg.logoUrl)}" alt="logo" onerror="this.style.display='none'">`
      : '';
    const screenshotBtn = cfg.enableScreenshot ? `
      <button class="${CLS.iconBtn} ${P}-ghost-btn" id="${ID.ssBtn}" title="Attach a screenshot">
        <svg viewBox="0 0 24 24"><path d="M9 3L7.17 5H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2h-3.17L15 3H9zm3 15c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z"/><circle cx="12" cy="13" r="2.5" opacity=".6"/></svg>
      </button>` : '';
    const chipsHTML = (cfg.suggestions || []).map(
      s => `<button class="${CLS.chip}" data-suggest="${escapeHTML(s)}">${escapeHTML(s)}</button>`
    ).join('');

    document.body.insertAdjacentHTML('beforeend', `
      <button id="${ID.toggle}" title="Open ${escapeHTML(cfg.botTitle)}">
        <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
      </button>

      <div id="${ID.panel}" role="dialog" aria-label="${escapeHTML(cfg.botTitle)}">
        <div id="${ID.header}">
          <button class="${P}-hdr-btn" id="${ID.hamburger}" title="Chat history" aria-label="Chat history">
            <svg viewBox="0 0 24 24"><path d="M3 6h18v2H3V6zm0 5h18v2H3v-2zm0 5h18v2H3v-2z"/></svg>
          </button>
          ${logoHTML}
          <span class="${P}-title">${escapeHTML(cfg.botTitle)}</span>
          <button class="${P}-hdr-btn" id="${ID.close}" title="Close" aria-label="Close">
            <svg viewBox="0 0 24 24"><path d="M18.3 5.71L12 12l6.3 6.29-1.41 1.42L10.59 13.4 4.3 19.71 2.88 18.3 9.17 12 2.88 5.71 4.3 4.29l6.29 6.3 6.3-6.3z"/></svg>
          </button>
        </div>

        <div id="${ID.body}">
          <aside id="${ID.sidebar}">
            <button id="${ID.newChat}">
              <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>
              New chat
            </button>
            <ul id="${ID.sidebarList}"></ul>
          </aside>

          <div id="${ID.chatArea}">
            <div id="${ID.messages}">
              <div id="${ID.empty}">
                <h2>${escapeHTML(cfg.greeting)}</h2>
                <div class="${P}-chips">${chipsHTML}</div>
              </div>
            </div>
            <div id="${ID.preview}">
              <img id="${ID.previewImg}" src="" alt="screenshot">
              <span>Screenshot attached</span>
              <button id="${ID.removeBtn}" title="Remove">&#x2715;</button>
            </div>
            <div id="${ID.inputRow}">
              ${screenshotBtn}
              <textarea id="${ID.input}" placeholder="Message ${escapeHTML(cfg.botTitle)}…" rows="1"></textarea>
              <button class="${CLS.iconBtn}" id="${ID.sendBtn}" title="Send">
                <svg viewBox="0 0 24 24"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    `);
  }

  injectCSS();
  buildDOM();

  // ── Element refs ────────────────────────────────────────────────────────────
  const el = id => document.getElementById(id);
  const panel = el(ID.panel), toggleBtn = el(ID.toggle), closeBtn = el(ID.close);
  const hamburger = el(ID.hamburger), sidebar = el(ID.sidebar);
  const sidebarList = el(ID.sidebarList), newChatBtn = el(ID.newChat);
  const messages = el(ID.messages), emptyState = el(ID.empty);
  const input = el(ID.input), sendBtn = el(ID.sendBtn), ssBtn = el(ID.ssBtn);
  const preview = el(ID.preview), previewImg = el(ID.previewImg), removeBtn = el(ID.removeBtn);
  const titleEl = panel.querySelector('.' + P + '-title');

  // ── Auth ───────────────────────────────────────────────────────────────────
  async function resolveToken() {
    if (cfg.authMode === 'none') return '';
    try {
      if (typeof cfg.getToken === 'function') return (await cfg.getToken()) || '';
      if (cfg.token) return cfg.token;
    } catch (e) { /* ignore — treat as anonymous */ }
    return '';
  }
  async function authHeaders(extra) {
    const token = await resolveToken();
    state.token = token;
    state.authed = !!token;
    const h = Object.assign({}, extra || {});
    if (token) h['Authorization'] = 'Bearer ' + token;
    return h;
  }
  function updateChromeForAuth() {
    const showSidebar = cfg.sidebarEnabled && state.authed;
    hamburger.style.display = showSidebar ? '' : 'none';
    if (!showSidebar) closeSidebar();
  }

  // ── Conversation list (sidebar) ──────────────────────────────────────────────
  async function loadConversations() {
    if (!state.authed || !cfg.sidebarEnabled) return;
    try {
      const res = await fetch(cfg.apiBase + '/conversations', { headers: await authHeaders() });
      if (!res.ok) return;
      state.conversations = await res.json();
      renderConversations();
    } catch (e) { /* offline / not authed */ }
  }

  function renderConversations() {
    sidebarList.innerHTML = '';
    state.conversations.forEach(c => {
      const li = document.createElement('li');
      li.className = CLS.convItem + (c.id === state.activeConversationId ? ' active' : '');
      li.dataset.cid = c.id;

      const title = document.createElement('span');
      title.className = P + '-conv-title';
      title.textContent = c.title || 'New chat';
      li.appendChild(title);

      const del = document.createElement('button');
      del.className = CLS.convDel;
      del.title = 'Delete';
      del.innerHTML = '&#x2715;';
      del.addEventListener('click', ev => { ev.stopPropagation(); deleteConversation(c.id); });
      li.appendChild(del);

      li.addEventListener('click', () => selectConversation(c.id));
      sidebarList.appendChild(li);
    });
  }

  async function selectConversation(id) {
    try {
      const res = await fetch(cfg.apiBase + '/conversations/' + encodeURIComponent(id) + '/messages',
        { headers: await authHeaders() });
      if (!res.ok) return;
      const detail = await res.json();
      state.activeConversationId = id;
      clearMessages();
      (detail.messages || []).forEach(m =>
        appendMessage(m.role === 'assistant' ? 'bot' : 'user', m.content));
      renderConversations();
      closeSidebar();
    } catch (e) { /* ignore */ }
  }

  async function deleteConversation(id) {
    try {
      await fetch(cfg.apiBase + '/conversations/' + encodeURIComponent(id),
        { method: 'DELETE', headers: await authHeaders() });
    } catch (e) { /* best effort */ }
    state.conversations = state.conversations.filter(c => c.id !== id);
    renderConversations();
    if (state.activeConversationId === id) newChat();
  }

  function newChat() {
    state.activeConversationId = null;
    clearMessages();
    renderConversations();
    closeSidebar();
  }

  // ── Sidebar open/close ───────────────────────────────────────────────────────
  function openSidebar()  { state.sidebarOpen = true;  sidebar.classList.add('open'); }
  function closeSidebar() { state.sidebarOpen = false; sidebar.classList.remove('open'); }
  function toggleSidebar(){ state.sidebarOpen ? closeSidebar() : openSidebar(); }

  // ── Messages ─────────────────────────────────────────────────────────────────
  function updateEmptyState() {
    const hasMsgs = messages.querySelector('.' + CLS.msg) !== null;
    emptyState.style.display = hasMsgs ? 'none' : '';
  }
  function clearMessages() {
    messages.querySelectorAll('.' + CLS.msg).forEach(n => n.remove());
    updateEmptyState();
  }
  function appendMessage(role, text, imgDataUrl) {
    const div = document.createElement('div');
    const roleClass = role === 'user' ? CLS.user : role === 'error' ? CLS.error : CLS.bot;
    div.className = CLS.msg + ' ' + roleClass;
    div.textContent = text;
    if (imgDataUrl) {
      const img = document.createElement('img');
      img.className = CLS.thumb; img.src = imgDataUrl;
      div.appendChild(img);
    }
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    updateEmptyState();
    return div;
  }
  function setWaiting(s) {
    state.isWaiting = s;
    sendBtn.disabled = s; input.disabled = s;
    if (ssBtn) ssBtn.disabled = s;
  }
  function clearAttachment() {
    state.attachedScreenshot = null;
    preview.style.display = 'none'; previewImg.src = '';
  }

  // ── Send ─────────────────────────────────────────────────────────────────────
  async function sendMessage(presetText) {
    if (state.isWaiting) return;
    const text = (presetText != null ? presetText : input.value).trim();
    if (!text && !state.attachedScreenshot) return;

    const userText = text || '(screenshot attached — please analyse)';
    const att = state.attachedScreenshot;
    const displayUrl = att ? 'data:' + att.mime + ';base64,' + att.base64 : null;

    appendMessage('user', userText, displayUrl);
    input.value = ''; input.style.height = 'auto';
    const typing = appendMessage('bot', 'Thinking…');
    typing.classList.add(CLS.typing);
    setWaiting(true);

    const wasNew = !state.activeConversationId;
    try {
      const body = { session_id: SESSION_ID, message: userText };
      if (state.activeConversationId) body.conversation_id = state.activeConversationId;
      if (att) { body.screenshot_base64 = att.base64; body.screenshot_mime = att.mime; }

      const res = await fetch(cfg.apiBase + '/chat', {
        method: 'POST',
        headers: await authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error('Server ' + res.status);
      const data = await res.json();
      typing.remove();
      appendMessage('bot', data.answer);

      if (data.conversation_id) state.activeConversationId = data.conversation_id;
      // Authenticated + a brand-new conversation: refresh the sidebar so the
      // background-generated short title shows up.
      if (state.authed && wasNew && data.conversation_id) {
        setTimeout(loadConversations, 1200);
      }
    } catch (err) {
      typing.remove();
      appendMessage('error', 'Sorry, something went wrong. Please try again.');
      if (window.console) console.warn('[chat-widget]', err);
    } finally {
      setWaiting(false);
      clearAttachment();
    }
  }

  // ── Screenshot capture (unchanged behaviour) ─────────────────────────────────
  async function takeScreenshot() {
    if (state.isWaiting) return;
    if (window.html2canvas) {
      try {
        panel.style.display = 'none';
        const canvas = await html2canvas(document.body, { useCORS: true, allowTaint: true, scale: 1, logging: false });
        panel.style.display = 'flex';
        storeScreenshot(canvas.toDataURL('image/png'));
        return;
      } catch (e) { panel.style.display = 'flex'; }
    }
    if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
      try {
        const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
        const track = stream.getVideoTracks()[0];
        const capture = new ImageCapture(track);
        const bitmap = await capture.grabFrame();
        track.stop();
        const canvas = document.createElement('canvas');
        canvas.width = bitmap.width; canvas.height = bitmap.height;
        canvas.getContext('2d').drawImage(bitmap, 0, 0);
        storeScreenshot(canvas.toDataURL('image/png'));
      } catch (e) {
        alert('Screenshot permission denied. You can also paste an image with Ctrl+V.');
      }
      return;
    }
    alert('Screenshot not available in this browser. Paste an image with Ctrl+V instead.');
  }
  function storeScreenshot(dataUrl) {
    const [header, base64] = dataUrl.split(',');
    const mime = header.replace('data:', '').replace(';base64', '');
    state.attachedScreenshot = { base64, mime };
    previewImg.src = dataUrl; preview.style.display = 'flex';
  }

  // ── Remote /config (title + screenshot availability) ─────────────────────────
  if (cfg.fetchRemoteConfig) {
    fetch(cfg.apiBase + '/config').then(r => r.ok ? r.json() : null).then(remote => {
      if (!remote) return;
      if (!userCfg.botTitle && !userCfg.title && remote.bot_name) titleEl.textContent = remote.bot_name;
      if (remote.screenshot_enabled === false && ssBtn) ssBtn.style.display = 'none';
    }).catch(() => {});
  }

  // ── Wiring ───────────────────────────────────────────────────────────────────
  toggleBtn.addEventListener('click', () => panel.classList.add('open'));
  closeBtn.addEventListener('click', () => panel.classList.remove('open'));
  hamburger.addEventListener('click', toggleSidebar);
  newChatBtn.addEventListener('click', newChat);
  sendBtn.addEventListener('click', () => sendMessage());
  if (ssBtn) ssBtn.addEventListener('click', takeScreenshot);
  if (removeBtn) removeBtn.addEventListener('click', clearAttachment);

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  input.addEventListener('paste', e => {
    for (const item of (e.clipboardData || {}).items || []) {
      if (item.type && item.type.startsWith('image/')) {
        e.preventDefault();
        const reader = new FileReader();
        reader.onload = ev => storeScreenshot(ev.target.result);
        reader.readAsDataURL(item.getAsFile());
        break;
      }
    }
  });
  emptyState.addEventListener('click', e => {
    const s = e.target && e.target.getAttribute && e.target.getAttribute('data-suggest');
    if (s) sendMessage(s);
  });

  // ── Boot ─────────────────────────────────────────────────────────────────────
  (async function boot() {
    await authHeaders();        // resolves state.authed
    updateChromeForAuth();
    updateEmptyState();
    await loadConversations();
  })();

  // ── Public API ───────────────────────────────────────────────────────────────
  window.GraphRagChat = {
    open: () => panel.classList.add('open'),
    close: () => panel.classList.remove('open'),
    send: text => sendMessage(text),
    newChat,
    refreshHistory: loadConversations,
    config: cfg,
    state,
  };
})();
