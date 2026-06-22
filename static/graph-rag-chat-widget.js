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
    // `{name}` is substituted with the signed-in username (or `anonymousName` when
    // anonymous). A greeting without the token still works — see renderGreeting().
    greeting:         "Hey {name}, what's on your mind today?",
    anonymousName:    'User',        // shown in the greeting before sign-in
    suggestions:      ['How can you help me browse?', 'What can you do?', 'Explain a topic'],
    // Auth
    getToken:         null,          // () => string | Promise<string>
    getUser:          null,          // optional () => {username} | Promise; else GET /me
    token:            '',            // static token alternative
    authMode:         'token',       // 'token' | 'none'
    sidebarEnabled:   true,
    // SSO sign-in: where the "Sign in" button sends an anonymous user. A string is
    // navigated to (with the current URL appended as `loginRedirectParam`); a
    // function is just called (e.g. keycloak-js `() => kc.login()`). '' → no button.
    loginUrl:           '',
    loginRedirectParam: 'destination',
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
    panelWidth:       420,
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
    headerNew:  `${P}-chat-header-new`,
    close:      `${P}-chat-close`,
    body:       `${P}-chat-body`,
    sidebar:    `${P}-chat-sidebar`,
    sidebarList:`${P}-chat-sidebar-list`,
    signinCard: `${P}-chat-signin`,
    signinBtn:  `${P}-chat-signin-btn`,
    historyHdr: `${P}-chat-history-header`,
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
    username: '',
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
      /* Reset inherited properties so the host page's font/color/line-height cannot
         leak across the shadow boundary, then re-establish the widget's own base.
         Non-inherited host-page rules (box-sizing, ul margins, img{display:block}, …)
         never cross the boundary at all. */
      :host { all: initial; display: block; }
      *, *::before, *::after { box-sizing: border-box; }
      svg { display: block; }

      /* ── Floating launcher ─────────────────────────────────────────────── */
      #${ID.toggle} {
        position: fixed; bottom: 24px; right: 24px; z-index: 2147483000;
        width: 60px; height: 60px; border-radius: 50%; padding: 0;
        background: #fff; border: 1px solid ${cfg.borderColor}; cursor: pointer;
        box-shadow: 0 8px 24px rgba(0,0,0,0.22);
        display: flex; align-items: center; justify-content: center; overflow: hidden;
        transition: transform .15s ease, box-shadow .2s ease;
      }
      #${ID.toggle}:hover { transform: scale(1.06); box-shadow: 0 10px 30px rgba(0,0,0,0.30); }
      .${P}-toggle-logo { width: 100%; height: 100%; object-fit: contain; padding: 3px; }
      .${P}-toggle-ico  { width: 32px; height: 32px; fill: ${cfg.accent}; }

      /* ── Side panel ────────────────────────────────────────────────────── */
      #${ID.panel} {
        position: fixed; top: 0; right: 0; z-index: 2147483001;
        width: ${cfg.panelWidth}px; max-width: 100vw; height: 100vh; height: 100dvh;
        background: ${cfg.panelBg}; color: ${cfg.textColor};
        box-shadow: -8px 0 40px rgba(0,0,0,0.18);
        display: flex; flex-direction: column; overflow: hidden;
        /* overflow:hidden clips the off-canvas sidebar (translateX(-102%)) to the
           panel box — without it the sidebar escapes left and shows on-screen even
           while the panel is slid away, covering the launcher button. */
        transform: translateX(105%); transition: transform .32s cubic-bezier(.4,0,.2,1);
        font-family: 'Segoe UI', system-ui, -apple-system, Roboto, sans-serif;
        font-size: 14px; line-height: 1.5;
      }
      #${ID.panel}.open { transform: translateX(0); }

      /* ── Header ────────────────────────────────────────────────────────── */
      #${ID.header} {
        display: flex; align-items: center; gap: 4px; flex-shrink: 0;
        background: ${cfg.headerBg}; padding: 10px 12px;
        border-bottom: 1px solid ${cfg.borderColor};
      }
      .${P}-hdr-btn {
        background: none; border: none; color: ${cfg.textColor}; cursor: pointer;
        width: 34px; height: 34px; padding: 6px; border-radius: 9px;
        display: inline-flex; align-items: center; justify-content: center; opacity: .8;
        transition: background .15s, opacity .15s;
      }
      .${P}-hdr-btn:hover { background: ${cfg.msgBotBg}; opacity: 1; }
      .${P}-hdr-btn svg { width: 21px; height: 21px; }
      .${P}-hdr-center {
        flex: 1; min-width: 0; display: flex; align-items: center;
        justify-content: center; gap: 8px;
      }
      .${P}-logo { width: 26px; height: 26px; object-fit: contain; border-radius: 5px; }
      .${P}-title { font-weight: 700; font-size: 16px; letter-spacing: .2px; white-space: nowrap; }

      /* ── Body / sidebar ────────────────────────────────────────────────── */
      #${ID.body} { flex: 1; position: relative; display: flex; min-height: 0; }
      #${ID.sidebar} {
        position: absolute; top: 0; left: 0; bottom: 0; width: 80%; max-width: 300px;
        background: ${cfg.sidebarBg}; border-right: 1px solid ${cfg.borderColor};
        display: flex; flex-direction: column; z-index: 5;
        transform: translateX(-102%); transition: transform .25s ease;
        box-shadow: 2px 0 16px rgba(0,0,0,0.10);
      }
      #${ID.sidebar}.open { transform: translateX(0); }
      #${ID.newChat} {
        margin: 12px; padding: 10px 12px; border: 1px solid ${cfg.borderColor};
        background: ${cfg.panelBg}; color: ${cfg.textColor}; border-radius: 12px;
        cursor: pointer; font-size: 14px; display: flex; align-items: center; gap: 8px;
      }
      #${ID.newChat}:hover { border-color: ${cfg.accent}; color: ${cfg.accent}; }
      #${ID.newChat} svg { width: 18px; height: 18px; }
      #${ID.sidebarList} { list-style: none; margin: 0; padding: 0 8px 12px; overflow-y: auto; flex: 1; }

      /* ── Sign-in card (anonymous users) ────────────────────────────────── */
      #${ID.signinCard} {
        margin: 4px 12px 14px; padding: 18px 16px; text-align: center;
        background: #eef4fe; border: 1px solid ${cfg.borderColor}; border-radius: 14px;
      }
      .${P}-signin-avatar {
        position: relative; width: 56px; height: 56px; margin: 2px auto 12px;
        border-radius: 50%; background: #dbe8fd; color: ${cfg.accent};
        display: flex; align-items: center; justify-content: center;
      }
      .${P}-signin-avatar > svg { width: 30px; height: 30px; }
      .${P}-signin-lock {
        position: absolute; right: -2px; bottom: -2px; width: 22px; height: 22px;
        border-radius: 50%; background: ${cfg.accent}; color: #fff; border: 2px solid #eef4fe;
        display: flex; align-items: center; justify-content: center;
      }
      .${P}-signin-lock svg { width: 12px; height: 12px; }
      .${P}-signin-title { font-size: 18px; font-weight: 700; color: ${cfg.textColor}; }
      .${P}-signin-sub {
        font-size: 13px; color: ${cfg.mutedColor}; margin: 6px 4px 14px; line-height: 1.45;
      }
      #${ID.signinBtn} {
        width: 100%; padding: 11px 14px; border: none; border-radius: 10px;
        background: ${cfg.accent}; color: #fff; font-size: 14px; font-weight: 600;
        cursor: pointer; display: inline-flex; align-items: center; justify-content: center; gap: 8px;
      }
      #${ID.signinBtn}:hover { background: ${cfg.accentHover}; }
      #${ID.signinBtn} svg { width: 18px; height: 18px; }

      /* ── "Chat History" section header (authenticated users) ───────────── */
      #${ID.historyHdr} {
        display: flex; align-items: center; gap: 8px; padding: 4px 18px 8px;
        font-size: 13px; font-weight: 700; color: ${cfg.textColor};
      }
      #${ID.historyHdr} svg { width: 16px; height: 16px; }
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

      /* ── Chat area ─────────────────────────────────────────────────────── */
      #${ID.chatArea} { flex: 1; display: flex; flex-direction: column; min-width: 0; }
      #${ID.messages} {
        flex: 1; overflow-y: auto; padding: 18px;
        display: flex; flex-direction: column; gap: 12px;
      }
      #${ID.empty} {
        flex: 1; display: flex; flex-direction: column; justify-content: space-between;
      }
      #${ID.empty} h2 {
        margin: 6px 2px 0; font-size: 30px; line-height: 1.22; font-weight: 800;
        color: ${cfg.textColor};
      }
      #${ID.empty} .${P}-chips { display: flex; flex-wrap: wrap; gap: 10px; padding: 0 2px 4px; }
      .${CLS.chip} {
        border: 1px solid ${cfg.borderColor}; background: ${cfg.panelBg};
        color: ${cfg.textColor}; border-radius: 20px; padding: 9px 16px;
        font-size: 13.5px; cursor: pointer; transition: border-color .15s, color .15s;
      }
      .${CLS.chip}:hover { border-color: ${cfg.accent}; color: ${cfg.accent}; }

      .${CLS.msg} {
        max-width: 86%; padding: 11px 14px; border-radius: 16px;
        font-size: 14px; line-height: 1.55; word-break: break-word;
      }
      .${CLS.user} { align-self: flex-end; background: ${cfg.msgUserBg}; color: #fff; border-bottom-right-radius: 5px; }
      .${CLS.bot}  { align-self: flex-start; background: ${cfg.msgBotBg}; color: ${cfg.textColor}; border-bottom-left-radius: 5px; white-space: pre-wrap; }
      .${CLS.error} { background: #fdecea; color: #b71c1c; align-self: flex-start; }
      .${CLS.thumb} { max-width: 100%; border-radius: 8px; margin-top: 6px; display: block; border: 1px solid ${cfg.borderColor}; }
      .${CLS.typing} { color: ${cfg.mutedColor}; font-style: italic; }

      /* ── Attachment preview ────────────────────────────────────────────── */
      #${ID.preview} {
        margin: 0 14px 6px; padding: 6px 10px; background: ${cfg.msgBotBg};
        border-radius: 10px; font-size: 12px; display: none; align-items: center; gap: 8px;
      }
      #${ID.preview} img { height: 42px; border-radius: 6px; }
      #${ID.removeBtn} { background: none; border: none; color: #d32f2f; font-size: 16px; cursor: pointer; margin-left: auto; }

      /* ── Composer ──────────────────────────────────────────────────────── */
      #${ID.inputRow} { padding: 12px 14px 16px; flex-shrink: 0; }
      .${P}-input-pill {
        display: flex; align-items: flex-end; gap: 6px;
        border: 1px solid ${cfg.borderColor}; border-radius: 26px;
        padding: 5px 6px 5px 8px; background: #fff;
        box-shadow: 0 1px 2px rgba(0,0,0,0.04); transition: border-color .15s, box-shadow .15s;
      }
      .${P}-input-pill:focus-within { border-color: ${cfg.accent}; box-shadow: 0 1px 6px rgba(21,101,192,0.15); }
      #${ID.input} {
        flex: 1; background: transparent; border: none; outline: none; resize: none;
        color: ${cfg.textColor}; font: inherit; font-size: 14px;
        padding: 10px 6px; min-height: 22px; max-height: 120px; overflow-y: auto;
      }
      #${ID.input}::placeholder { color: ${cfg.placeholderColor}; }
      .${CLS.iconBtn} {
        flex-shrink: 0; width: 40px; height: 40px; border-radius: 50%;
        border: none; cursor: pointer; background: transparent; color: ${cfg.mutedColor};
        display: inline-flex; align-items: center; justify-content: center;
        transition: background .2s, color .2s;
      }
      .${CLS.iconBtn}:hover { background: ${cfg.msgBotBg}; color: ${cfg.textColor}; }
      .${CLS.iconBtn} svg { width: 22px; height: 22px; }
      .${CLS.iconBtn}:disabled { opacity: .45; cursor: not-allowed; }
      .${P}-send { background: ${cfg.accent}; color: #fff; }
      .${P}-send:hover { background: ${cfg.accentHover}; color: #fff; }

      /* ── Accessibility: visible keyboard focus (never removed) ─────────── */
      :focus-visible { outline: 2px solid ${cfg.accent}; outline-offset: 2px; }
      #${ID.input}:focus-visible { outline: none; }   /* pill shows focus-within */

      /* ── Typing indicator (3-dot pulse — AI-native signature) ──────────── */
      .${P}-dots { display: inline-flex; align-items: center; gap: 4px; }
      .${P}-dots span {
        width: 7px; height: 7px; border-radius: 50%; background: ${cfg.mutedColor};
        animation: ${P}-blink 1.4s infinite both;
      }
      .${P}-dots span:nth-child(2) { animation-delay: .2s; }
      .${P}-dots span:nth-child(3) { animation-delay: .4s; }
      @keyframes ${P}-blink {
        0%, 80%, 100% { opacity: .25; transform: translateY(0); }
        40%           { opacity: 1;   transform: translateY(-3px); }
      }

      /* ── Subtle message entrance ───────────────────────────────────────── */
      .${CLS.msg} { animation: ${P}-msg-in .24s ease-out both; }
      @keyframes ${P}-msg-in {
        from { opacity: 0; transform: translateY(6px); }
        to   { opacity: 1; transform: translateY(0); }
      }

      /* ── Honour the OS "reduce motion" setting ─────────────────────────── */
      @media (prefers-reduced-motion: reduce) {
        #${ID.panel}, #${ID.sidebar}, #${ID.toggle},
        .${CLS.chip}, .${CLS.iconBtn}, .${P}-hdr-btn { transition: none !important; }
        .${CLS.msg} { animation: none !important; }
        .${P}-dots span { animation: none !important; opacity: .55; transform: none; }
      }
    `;
    root.appendChild(style);
  }

  function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }
  function escapeRegExp(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  // Icon glyphs (outline icons use stroke; filled use fill — set inline so the
  // shadow stylesheet never has to special-case fill vs stroke per button).
  const ICON = {
    bubble:  `<path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/>`,
    sidebar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="9.5" y1="4" x2="9.5" y2="20"/></svg>`,
    edit:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>`,
    close:   `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12 19 6.41z"/></svg>`,
    plus:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
    send:    `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3.4 20.4 21 12 3.4 3.6 3.4 10.1 15 12 3.4 13.9z"/></svg>`,
    user:    `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>`,
    lock:    `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 1a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2h-1V6a5 5 0 0 0-5-5zm3 8H9V6a3 3 0 0 1 6 0v3z"/></svg>`,
    clock:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
  };

  function buildDOM() {
    const toggleInner = cfg.logoUrl
      ? `<img class="${P}-toggle-logo" src="${escapeHTML(cfg.logoUrl)}" alt=""
              onerror="this.style.display='none';this.nextElementSibling.style.display='block'">
         <svg class="${P}-toggle-ico" style="display:none" viewBox="0 0 24 24">${ICON.bubble}</svg>`
      : `<svg class="${P}-toggle-ico" viewBox="0 0 24 24">${ICON.bubble}</svg>`;

    const logoHTML = cfg.logoUrl
      ? `<img class="${P}-logo" src="${escapeHTML(cfg.logoUrl)}" alt="logo" onerror="this.style.display='none'">`
      : '';

    const attachBtn = cfg.enableScreenshot ? `
      <button class="${CLS.iconBtn}" id="${ID.ssBtn}" title="Attach a screenshot" aria-label="Attach a screenshot">${ICON.plus}</button>` : '';

    const chipsHTML = (cfg.suggestions || []).map(
      s => `<button class="${CLS.chip}" data-suggest="${escapeHTML(s)}">${escapeHTML(s)}</button>`
    ).join('');

    const tpl = document.createElement('template');
    tpl.innerHTML = `
      <button id="${ID.toggle}" title="Open ${escapeHTML(cfg.botTitle)}" aria-label="Open ${escapeHTML(cfg.botTitle)}">
        ${toggleInner}
      </button>

      <div id="${ID.panel}" role="dialog" aria-label="${escapeHTML(cfg.botTitle)}">
        <div id="${ID.header}">
          <button class="${P}-hdr-btn" id="${ID.hamburger}" title="Chat history" aria-label="Chat history">${ICON.sidebar}</button>
          <button class="${P}-hdr-btn" id="${ID.headerNew}" title="New chat" aria-label="New chat">${ICON.edit}</button>
          <div class="${P}-hdr-center">
            ${logoHTML}
            <span class="${P}-title">${escapeHTML(cfg.botTitle)}</span>
          </div>
          <button class="${P}-hdr-btn" id="${ID.close}" title="Close" aria-label="Close">${ICON.close}</button>
        </div>

        <div id="${ID.body}">
          <aside id="${ID.sidebar}">
            <button id="${ID.newChat}">${ICON.edit} New chat</button>
            <div id="${ID.signinCard}" style="display:none">
              <div class="${P}-signin-avatar">
                ${ICON.user}<span class="${P}-signin-lock">${ICON.lock}</span>
              </div>
              <div class="${P}-signin-title">Sign in</div>
              <div class="${P}-signin-sub">Sign in to enable persistent chat history and personalized experiences.</div>
              <button id="${ID.signinBtn}">${ICON.user} Sign in</button>
            </div>
            <div id="${ID.historyHdr}" style="display:none">${ICON.clock}<span>Chat History</span></div>
            <ul id="${ID.sidebarList}"></ul>
          </aside>

          <div id="${ID.chatArea}">
            <div id="${ID.messages}" aria-live="polite" aria-relevant="additions">
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
              <div class="${P}-input-pill">
                ${attachBtn}
                <textarea id="${ID.input}" placeholder="Message ${escapeHTML(cfg.botTitle)}…" rows="1" aria-label="Message ${escapeHTML(cfg.botTitle)}"></textarea>
                <button class="${CLS.iconBtn} ${P}-send" id="${ID.sendBtn}" title="Send" aria-label="Send">${ICON.send}</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
    root.appendChild(tpl.content);
  }

  // ── Shadow-DOM host — isolates the widget from the host page's CSS ───────────
  // Drupal/Olivero (and most CMS themes) ship global rules — *{box-sizing},
  // ul{margin-inline-start}, img{display:block;height:auto}, button{font-family},
  // plus admin-toolbar/contrib styles — that otherwise bleed in and break the
  // widget's layout. A shadow root gives the widget a private styling scope so it
  // renders identically on any site. The prefix/IDs still come from cfg — nothing
  // is hardcoded here.
  const hostEl = document.createElement('div');
  hostEl.id = `${P}-chat-root`;
  document.body.appendChild(hostEl);
  const root = hostEl.attachShadow({ mode: 'open' });

  injectCSS();
  buildDOM();

  // ── Element refs (scoped to the shadow root) ─────────────────────────────────
  const el = id => root.getElementById(id);
  const panel = el(ID.panel), toggleBtn = el(ID.toggle), closeBtn = el(ID.close);
  const hamburger = el(ID.hamburger), sidebar = el(ID.sidebar);
  const sidebarList = el(ID.sidebarList), newChatBtn = el(ID.newChat);
  const signinCard = el(ID.signinCard), signinBtn = el(ID.signinBtn);
  const historyHeader = el(ID.historyHdr);
  const headerNewBtn = el(ID.headerNew);
  const messages = el(ID.messages), emptyState = el(ID.empty);
  const greetingEl = emptyState.querySelector('h2');
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
  // A sign-in path exists when auth isn't disabled and a login target is set.
  function canSignIn() {
    return !state.authed && cfg.authMode !== 'none' && !!cfg.loginUrl;
  }
  function updateChromeForAuth() {
    // The sidebar toggle is available to authenticated users (their history) and to
    // anonymous users when a sign-in path exists (so they can reach the Sign-in card).
    const showToggle = cfg.sidebarEnabled && (state.authed || canSignIn());
    hamburger.style.display = showToggle ? '' : 'none';
    if (!showToggle) closeSidebar();
    // Toggle the sidebar's two faces: Sign-in card (anonymous) vs. history (authed).
    if (signinCard) signinCard.style.display = canSignIn() ? '' : 'none';
    updateHistoryHeader();
  }
  // "Chat History" header only makes sense once an authed user has conversations.
  function updateHistoryHeader() {
    if (historyHeader) {
      historyHeader.style.display =
        state.authed && state.conversations.length > 0 ? '' : 'none';
    }
  }
  // Begin SSO login. A function loginUrl is called as-is (e.g. keycloak-js
  // `kc.login()`); a string is navigated to with the current page appended so the
  // portal returns the user here after authenticating.
  function signIn() {
    const target = cfg.loginUrl;
    if (!target) return;
    if (typeof target === 'function') { target(); return; }
    const sep = target.indexOf('?') === -1 ? '?' : '&';
    const url = target + sep + encodeURIComponent(cfg.loginRedirectParam) +
                '=' + encodeURIComponent(location.href);
    window.location.assign(url);
  }
  // Resolve the display name for the greeting: a getUser() override if provided,
  // else GET /me (which applies the server-side JWT_FIELD_USERNAME mapping).
  async function resolveUser() {
    if (!state.authed) { state.username = ''; return; }
    try {
      if (typeof cfg.getUser === 'function') {
        const u = await cfg.getUser();
        state.username = (u && (u.username || u.name)) || '';
      } else {
        const res = await fetch(cfg.apiBase + '/me', { headers: await authHeaders() });
        if (res.ok) {
          state.username = (await res.json()).username || '';
        } else if (res.status === 401 || res.status === 403) {
          // Token present but rejected (expired/invalid). Revert to signed-out so the
          // Sign-in card returns, instead of showing a half-authenticated state
          // (no Sign-in button yet greeting stuck on the anonymous name).
          state.authed = false;
          state.token = '';
          state.username = '';
          updateChromeForAuth();
        }
      }
    } catch (e) { /* fall back to anonymousName */ }
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
    updateHistoryHeader();
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
      del.setAttribute('aria-label', 'Delete conversation');
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

  // ── Panel + sidebar open/close ───────────────────────────────────────────────
  function openPanel()  { panel.classList.add('open');    toggleBtn.style.display = 'none'; }
  function closePanel() { panel.classList.remove('open'); toggleBtn.style.display = ''; }
  function openSidebar()  { state.sidebarOpen = true;  sidebar.classList.add('open'); }
  function closeSidebar() { state.sidebarOpen = false; sidebar.classList.remove('open'); }
  function toggleSidebar(){ state.sidebarOpen ? closeSidebar() : openSidebar(); }

  // ── Messages ─────────────────────────────────────────────────────────────────
  function updateEmptyState() {
    const hasMsgs = messages.querySelector('.' + CLS.msg) !== null;
    emptyState.style.display = hasMsgs ? 'none' : '';
  }
  // Personalize the empty-state greeting. Prefers the `{name}` token; otherwise
  // (back-compat with a literal "Hey User, …") swaps the standalone anonymousName
  // word once. Anonymous → anonymousName ("User"); signed-in → the username.
  function renderGreeting() {
    if (!greetingEl) return;
    const name = state.authed ? (state.username || cfg.anonymousName) : cfg.anonymousName;
    const g = cfg.greeting || '';
    let out;
    if (g.indexOf('{name}') !== -1) {
      out = g.replace(/\{name\}/g, name);
    } else if (name && name !== cfg.anonymousName) {
      out = g.replace(new RegExp('\\b' + escapeRegExp(cfg.anonymousName) + '\\b'), name);
    } else {
      out = g;
    }
    greetingEl.textContent = out;
  }
  function clearMessages() {
    messages.querySelectorAll('.' + CLS.msg).forEach(n => n.remove());
    updateEmptyState();
  }
  // Animated 3-dot "typing" bubble. aria-hidden so the polite live region only
  // announces the real answer that replaces it, not the placeholder.
  function showTyping() {
    const div = document.createElement('div');
    div.className = CLS.msg + ' ' + CLS.bot + ' ' + CLS.typing;
    div.setAttribute('aria-hidden', 'true');
    div.innerHTML = `<span class="${P}-dots"><span></span><span></span><span></span></span>`;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    updateEmptyState();
    return div;
  }
  function appendMessage(role, text, imgDataUrl) {
    const div = document.createElement('div');
    const roleClass = role === 'user' ? CLS.user : role === 'error' ? CLS.error : CLS.bot;
    div.className = CLS.msg + ' ' + roleClass;
    if (role === 'error') div.setAttribute('role', 'alert');   // assertive announce
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
    const typing = showTyping();
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

  // ── Remote /config (title + screenshot availability + sign-in path) ──────────
  if (cfg.fetchRemoteConfig) {
    fetch(cfg.apiBase + '/config').then(r => r.ok ? r.json() : null).then(remote => {
      if (!remote) return;
      if (!userCfg.botTitle && !userCfg.title && remote.bot_name) titleEl.textContent = remote.bot_name;
      if (remote.screenshot_enabled === false && ssBtn) ssBtn.style.display = 'none';
      // Adopt the server-configured login URL unless the page set one explicitly,
      // then re-evaluate chrome (a sign-in path may now exist for anonymous users).
      if (!userCfg.loginUrl && remote.login_url) {
        cfg.loginUrl = remote.login_url;
        updateChromeForAuth();
      }
    }).catch(() => {});
  }

  // ── Wiring ───────────────────────────────────────────────────────────────────
  toggleBtn.addEventListener('click', openPanel);
  closeBtn.addEventListener('click', closePanel);
  hamburger.addEventListener('click', toggleSidebar);
  newChatBtn.addEventListener('click', newChat);
  if (headerNewBtn) headerNewBtn.addEventListener('click', newChat);
  if (signinBtn) signinBtn.addEventListener('click', signIn);
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
    renderGreeting();           // anonymous → uses anonymousName ("User")
    if (state.authed) {         // probe /me first: sets the username, or downgrades to
      await resolveUser();      // anonymous if the token is rejected (expired/invalid)
      updateChromeForAuth();
      renderGreeting();
    }
    await loadConversations();  // no-op unless still authed after the /me probe
  })();

  // ── Public API ───────────────────────────────────────────────────────────────
  window.GraphRagChat = {
    open: openPanel,
    close: closePanel,
    send: text => sendMessage(text),
    newChat,
    signIn,
    refreshHistory: loadConversations,
    config: cfg,
    state,
  };
})();
