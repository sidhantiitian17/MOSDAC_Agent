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
    // Base URL for the vendored KaTeX assets (katex.min.css/js + fonts). Empty →
    // derived from this script's own URL, so it works at any mount path.
    katexBase:        '',
  };

  const userCfg = window.GRAPH_RAG_CHAT_CONFIG || {};
  const cfg = Object.assign({}, DEFAULTS, userCfg);
  // Back-compat aliases (older config used title/botLogo).
  if (!userCfg.botTitle && userCfg.title) cfg.botTitle = userCfg.title;
  if (!userCfg.logoUrl && userCfg.botLogo) cfg.logoUrl = userCfg.botLogo;

  // Resolve where sibling assets (vendored KaTeX) live. document.currentScript is
  // valid here (we're still in the synchronous top-level of the script), so we can
  // derive `<dir>/vendor/katex` from this file's own URL — no hardcoded path.
  const WIDGET_SRC = (document.currentScript && document.currentScript.src) || '';
  const KATEX_BASE = cfg.katexBase ||
    (WIDGET_SRC ? WIDGET_SRC.replace(/[^/]*\.js(\?.*)?$/, 'vendor/katex') : '/static/vendor/katex');

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
    localTitles: {},   // conversationId -> title derived from first user message
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

      /* ── Rendered Markdown answer (formatted bot bubble) ───────────────────
         The base bot bubble keeps white-space:pre-wrap for the live streaming
         preview; the formatted final answer carries .${P}-md, which switches to
         normal flow so headings/lists/tables lay out correctly. */
      .${P}-md { white-space: normal; }
      .${P}-md > *:first-child { margin-top: 0; }
      .${P}-md > *:last-child  { margin-bottom: 0; }
      .${P}-md p { margin: 0 0 10px; }
      .${P}-md h3, .${P}-md h4, .${P}-md h5, .${P}-md h6 {
        margin: 14px 0 6px; line-height: 1.3; font-weight: 700; color: ${cfg.textColor};
      }
      .${P}-md h3 { font-size: 16px; }
      .${P}-md h4 { font-size: 15px; }
      .${P}-md h5, .${P}-md h6 { font-size: 14px; }
      .${P}-md ul, .${P}-md ol { margin: 6px 0 10px; padding-left: 22px; }
      .${P}-md li { margin: 3px 0; }
      .${P}-md li > p { margin: 0; }
      .${P}-md a { color: ${cfg.accent}; text-decoration: underline; word-break: break-all; }
      .${P}-md strong { font-weight: 700; }
      .${P}-md code {
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        font-size: 12.5px; background: rgba(0,0,0,.06); padding: 1px 5px; border-radius: 5px;
      }
      .${P}-md pre {
        margin: 8px 0; padding: 10px 12px; background: #0f172a; color: #e2e8f0;
        border-radius: 10px; overflow-x: auto;
      }
      .${P}-md pre code { background: none; color: inherit; padding: 0; font-size: 12.5px; line-height: 1.5; }
      .${P}-md blockquote {
        margin: 8px 0; padding: 4px 12px; border-left: 3px solid ${cfg.accent};
        color: ${cfg.mutedColor};
      }
      .${P}-md hr { border: none; border-top: 1px solid ${cfg.borderColor}; margin: 12px 0; }
      .${P}-md table {
        border-collapse: collapse; margin: 10px 0; font-size: 13px;
        display: block; max-width: 100%; overflow-x: auto;
      }
      .${P}-md th, .${P}-md td { border: 1px solid ${cfg.borderColor}; padding: 6px 10px; text-align: left; }
      .${P}-md th { background: ${cfg.msgBotBg}; font-weight: 700; }
      .${P}-math-block {
        display: block; margin: 10px 0; padding: 8px 12px; overflow-x: auto;
        background: rgba(0,0,0,.04); border-radius: 8px; text-align: center;
        white-space: pre-wrap; font-size: 15px;
        font-family: 'Cambria Math', 'Latin Modern Math', 'Times New Roman', serif;
      }
      .${P}-math-inline {
        font-family: 'Cambria Math', 'Latin Modern Math', 'Times New Roman', serif;
      }
      /* KaTeX typeset output (katex.min.css is injected into the shadow root). Let
         wide display equations scroll inside the narrow panel instead of clipping. */
      .${P}-md .katex { color: ${cfg.textColor}; font-size: 1.05em; }
      .${P}-md .katex-display { margin: 12px 0; overflow-x: auto; overflow-y: hidden; padding: 2px 0; }

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
        0%, 80%, 100% { opacity: .25; transform: translateY(0) scale(1); }
        40%           { opacity: 1;   transform: translateY(-5px) scale(1.15); }
      }
      /* Reduced-motion fallback: a gentle opacity-only pulse (no positional motion)
         so the indicator still reads as "working" instead of sitting frozen. */
      @keyframes ${P}-fade {
        0%, 80%, 100% { opacity: .3; }
        40%           { opacity: 1; }
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
        .${P}-dots span { animation: ${P}-fade 1.4s infinite both !important; transform: none !important; }
      }
    `;
    root.appendChild(style);
  }

  function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  // ── Markdown + math rendering ────────────────────────────────────────────────
  // The backend answers in Markdown (Docling-parsed documents bring headings,
  // bold/italics, lists, code blocks, tables and LaTeX math). The widget used to
  // drop that source straight into `textContent`, so users saw raw `**bold**`,
  // `$$…$$` and `\(…\)` markup instead of a formatted answer. renderMarkdown()
  // turns it into clean, safe HTML. It is deliberately self-contained — no
  // external library — so it keeps working in the air-gapped deployment. Every
  // piece of model/user text is HTML-escaped before any tag is emitted and only a
  // fixed whitelist of tags is produced, so the answer text cannot inject markup.

  const PH = '';                       // private-use delimiter for placeholders
  function makePlaceholder(kind, i) { return PH + kind + i + PH; }

  // Allow http(s)/mailto and same-page/relative links; block javascript:, data:, …
  function safeUrl(url) {
    const u = String(url).trim();
    if (/^(https?:|mailto:)/i.test(u)) return u;
    if (/^[#/]/.test(u) || /^[\w.\-]+(\/|$)/.test(u)) return u;   // anchor / relative
    return '#';
  }

  // Unicode super/subscripts + Greek letters, for light LaTeX prettification when
  // no math typesetter is bundled. Anything not in the maps falls back gracefully.
  const SUP = { '0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹',
    '+':'⁺','-':'⁻','=':'⁼','(':'⁽',')':'⁾','n':'ⁿ','i':'ⁱ' };
  const SUB = { '0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉',
    '+':'₊','-':'₋','=':'₌','(':'₍',')':'₎','a':'ₐ','e':'ₑ','h':'ₕ','i':'ᵢ','j':'ⱼ','k':'ₖ',
    'l':'ₗ','m':'ₘ','n':'ₙ','o':'ₒ','p':'ₚ','r':'ᵣ','s':'ₛ','t':'ₜ','u':'ᵤ','v':'ᵥ','x':'ₓ' };
  const SYM = { sum:'∑', int:'∫', oint:'∮', prod:'∏', partial:'∂', infty:'∞', exp:'exp',
    log:'log', ln:'ln', sin:'sin', cos:'cos', tan:'tan', lim:'lim', pm:'±', mp:'∓',
    times:'×', cdot:'·', div:'÷', ast:'∗', star:'⋆', le:'≤', leq:'≤', ge:'≥', geq:'≥',
    neq:'≠', equiv:'≡', approx:'≈', sim:'∼', propto:'∝', nabla:'∇', forall:'∀', exists:'∃',
    in:'∈', notin:'∉', subset:'⊂', supset:'⊃', cup:'∪', cap:'∩', to:'→', gets:'←',
    rightarrow:'→', leftarrow:'←', Rightarrow:'⇒', Leftarrow:'⇐', leftrightarrow:'↔',
    langle:'⟨', rangle:'⟩', lfloor:'⌊', rfloor:'⌋', ldots:'…', cdots:'⋯', dots:'…',
    alpha:'α', beta:'β', gamma:'γ', delta:'δ', epsilon:'ε', varepsilon:'ε', zeta:'ζ',
    eta:'η', theta:'θ', vartheta:'ϑ', iota:'ι', kappa:'κ', lambda:'λ', mu:'μ', nu:'ν',
    xi:'ξ', pi:'π', rho:'ρ', sigma:'σ', tau:'τ', upsilon:'υ', phi:'φ', varphi:'φ',
    chi:'χ', psi:'ψ', omega:'ω', Gamma:'Γ', Delta:'Δ', Theta:'Θ', Lambda:'Λ', Xi:'Ξ',
    Pi:'Π', Sigma:'Σ', Phi:'Φ', Psi:'Ψ', Omega:'Ω' };

  function toScript(s, map) {
    let out = '';
    for (const ch of s) { if (map[ch] == null) return null; out += map[ch]; }
    return out;
  }

  // Best-effort LaTeX → readable Unicode. Not a full typesetter, but turns the
  // common scientific notation the corpus produces into something legible instead
  // of raw `\frac{}{}`, `\sum_{}` and `\lambda` source.
  function prettifyMath(tex) {
    let t = String(tex);
    t = t.replace(/\\(left|right|big|Big|bigg|Bigg|displaystyle|textstyle|limits|,|;|:|!|quad|qquad)\b/g, ' ');
    t = t.replace(/\\\\/g, '   ');                       // in-math line break → spaces
    t = t.replace(/\\text\s*\{([^{}]*)\}/g, '$1');
    for (let n = 0; n < 4; n++) {                        // resolve simple nesting
      t = t.replace(/\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, '($1)/($2)');
      t = t.replace(/\\sqrt\s*\{([^{}]*)\}/g, '√($1)');
    }
    t = t.replace(/\\([A-Za-z]+)/g, (m, name) => SYM[name] != null ? SYM[name] : name);
    t = t.replace(/\^\{([^{}]*)\}|\^(\S)/g, (m, a, b) => {
      const s = a != null ? a : b; return toScript(s, SUP) || '^(' + s + ')';
    });
    t = t.replace(/_\{([^{}]*)\}|_(\S)/g, (m, a, b) => {
      const s = a != null ? a : b;
      return toScript(s, SUB) || (a != null ? '_(' + s + ')' : '_' + s);
    });
    t = t.replace(/[{}]/g, '');
    return t.replace(/[ \t]{2,}/g, ' ').trim();
  }

  // Inline span → escaped HTML with emphasis + links. `s` is raw markdown text;
  // it is escaped first so any literal markup in it is inert.
  function renderInline(s) {
    s = escapeHTML(s);
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, txt, url) =>
      '<a href="' + escapeHTML(safeUrl(url)) + '" target="_blank" rel="noopener noreferrer">' + txt + '</a>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    s = s.replace(/(^|[^\w_])_([^_\n]+)_(?![\w_])/g, '$1<em>$2</em>');
    s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    return s;
  }

  function renderMarkdown(src) {
    if (src == null) return '';
    let text = String(src).replace(/\r\n?/g, '\n');
    const codeBlocks = [], mathSpans = [], inlineCodes = [];
    // Math is preserved verbatim (delimiters included) and only HTML-escaped — never
    // markdown-processed — so `\frac{}{}`, `_`, `^`, `*` inside formulas survive.
    // KaTeX auto-render typesets it once the HTML is in the DOM (see typesetMath).
    const stashMath = m => { mathSpans.push(escapeHTML(m)); return makePlaceholder('M', mathSpans.length - 1); };

    // Pull out content that markdown/escaping must not touch, leaving placeholders.
    text = text.replace(/```[^\n]*\n([\s\S]*?)```/g, (m, code) => {
      codeBlocks.push(escapeHTML(code.replace(/\n$/, '')));
      return '\n' + makePlaceholder('C', codeBlocks.length - 1) + '\n';
    });
    text = text.replace(/\$\$[\s\S]+?\$\$/g, stashMath);     // display math: $$ … $$
    text = text.replace(/\\\[[\s\S]+?\\\]/g, stashMath);     // display math: \[ … \]
    text = text.replace(/\\\([\s\S]+?\\\)/g, stashMath);     // inline math:  \( … \)
    text = text.replace(/`([^`\n]+)`/g, (m, code) => {
      inlineCodes.push(escapeHTML(code)); return makePlaceholder('I', inlineCodes.length - 1);
    });

    // Block-level pass over raw lines (markdown markers like #, >, |, - intact).
    const lines = text.split('\n');
    const out = [];
    let para = [];
    const flushPara = () => { if (para.length) { out.push('<p>' + para.map(renderInline).join('<br>') + '</p>'); para = []; } };
    const splitRow = r => r.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());

    let k = 0;
    while (k < lines.length) {
      const line = lines[k], t = line.trim();
      if (new RegExp('^' + PH + '[CM]\\d+' + PH + '$').test(t)) { flushPara(); out.push(t); k++; continue; }
      if (!t) { flushPara(); k++; continue; }
      const h = t.match(/^(#{1,6})\s+(.*)$/);
      if (h) { flushPara(); const lvl = Math.min(h[1].length + 2, 6); out.push('<h' + lvl + '>' + renderInline(h[2]) + '</h' + lvl + '>'); k++; continue; }
      if (/^([-*_])(\s*\1){2,}$/.test(t)) { flushPara(); out.push('<hr>'); k++; continue; }
      // Markdown table: a header row followed by a |---|---| separator row.
      if (t.indexOf('|') !== -1 && k + 1 < lines.length &&
          /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$/.test(lines[k + 1])) {
        flushPara();
        const head = splitRow(t); k += 2; const rows = [];
        while (k < lines.length && lines[k].trim() && lines[k].indexOf('|') !== -1) { rows.push(splitRow(lines[k])); k++; }
        let tbl = '<table><thead><tr>' + head.map(c => '<th>' + renderInline(c) + '</th>').join('') + '</tr></thead>';
        if (rows.length) tbl += '<tbody>' + rows.map(r => '<tr>' + r.map(c => '<td>' + renderInline(c) + '</td>').join('') + '</tr>').join('') + '</tbody>';
        out.push(tbl + '</table>'); continue;
      }
      if (/^>\s?/.test(t)) {
        flushPara(); const q = [];
        while (k < lines.length && /^>\s?/.test(lines[k].trim())) { q.push(lines[k].trim().replace(/^>\s?/, '')); k++; }
        out.push('<blockquote>' + q.map(renderInline).join('<br>') + '</blockquote>'); continue;
      }
      if (/^[-*+]\s+/.test(t)) {
        flushPara(); const items = [];
        while (k < lines.length && /^[-*+]\s+/.test(lines[k].trim())) { items.push('<li>' + renderInline(lines[k].trim().replace(/^[-*+]\s+/, '')) + '</li>'); k++; }
        out.push('<ul>' + items.join('') + '</ul>'); continue;
      }
      if (/^\d+[.)]\s+/.test(t)) {
        flushPara(); const items = [];
        while (k < lines.length && /^\d+[.)]\s+/.test(lines[k].trim())) { items.push('<li>' + renderInline(lines[k].trim().replace(/^\d+[.)]\s+/, '')) + '</li>'); k++; }
        out.push('<ol>' + items.join('') + '</ol>'); continue;
      }
      para.push(t); k++;
    }
    flushPara();

    // Re-insert the protected fragments.
    return out.join('\n')
      .replace(new RegExp(PH + 'C(\\d+)' + PH, 'g'), (m, i) => '<pre><code>' + codeBlocks[+i] + '</code></pre>')
      .replace(new RegExp(PH + 'M(\\d+)' + PH, 'g'), (m, i) => mathSpans[+i])
      .replace(new RegExp(PH + 'I(\\d+)' + PH, 'g'), (m, i) => '<code>' + inlineCodes[+i] + '</code>');
  }

  // Short, human-readable title derived from a user's first message. Used as an
  // instant, optimistic sidebar label so a new chat is never stuck on "New chat"
  // (e.g. when the backend skips LLM titling for a refused answer). The real
  // LLM-generated title, when produced, overrides this in renderConversations().
  function deriveTitle(text) {
    const t = String(text).replace(/\s+/g, ' ').trim();
    if (!t) return 'New chat';
    return t.length > 40 ? t.slice(0, 40).trimEnd() + '…' : t;
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

  // ── KaTeX (math typesetting) — vendored, lazy-loaded, air-gapped ─────────────
  // Loaded once on demand from the same-origin /static/vendor/katex bundle. The
  // stylesheet is injected INTO the shadow root so KaTeX's classes style the
  // typeset spans here and the CSS's relative font URLs resolve to
  // /static/vendor/katex/fonts (same-origin; CSP default-src 'self').
  let _katexPromise = null;
  function loadScriptOnce(src) {
    return new Promise((resolve, reject) => {
      let s = document.querySelector('script[data-grag-katex][src="' + src + '"]');
      if (s && s.dataset.loaded) { resolve(); return; }
      if (!s) {
        s = document.createElement('script');
        s.src = src; s.async = false; s.setAttribute('data-grag-katex', '');
        document.head.appendChild(s);
      }
      s.addEventListener('load', () => { s.dataset.loaded = '1'; resolve(); });
      s.addEventListener('error', () => reject(new Error('load failed: ' + src)));
    });
  }
  function appendStylesheet(parent, href) {
    if (parent.querySelector('link[data-grag-katex][href="' + href + '"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet'; link.href = href; link.setAttribute('data-grag-katex', '');
    parent.appendChild(link);
  }
  function ensureKatex() {
    if (_katexPromise) return _katexPromise;
    _katexPromise = (async () => {
      const href = KATEX_BASE + '/katex.min.css';
      // Shadow-root copy: KaTeX's .katex* layout classes must live inside the shadow
      // tree to style the typeset spans (document CSS doesn't cross the boundary).
      // Document-head copy: registers KaTeX's @font-face globally, the portable way
      // to make the math fonts reach shadow content across browsers.
      appendStylesheet(root, href);
      appendStylesheet(document.head, href);
      await loadScriptOnce(KATEX_BASE + '/katex.min.js');
      await loadScriptOnce(KATEX_BASE + '/contrib/auto-render.min.js');
      if (typeof window.renderMathInElement !== 'function') throw new Error('KaTeX auto-render unavailable');
      return window.renderMathInElement;
    })();
    _katexPromise.catch(() => {});                   // callers handle failure via fallback
    return _katexPromise;
  }
  const KATEX_DELIMS = [
    { left: '$$', right: '$$', display: true },
    { left: '\\[', right: '\\]', display: true },
    { left: '\\(', right: '\\)', display: false },
    // No single-`$` delimiter on purpose: it would mis-typeset prose like "$5 … $10".
  ];
  // Typeset any math in `div`. We only pay the KaTeX cost when a delimiter is
  // actually present. If KaTeX can't load (assets missing/blocked), degrade to
  // readable Unicode via prettifyFallback rather than leaving raw LaTeX on screen.
  function typesetMath(div) {
    if (!/\$\$|\\\(|\\\[/.test(div.textContent)) return;
    ensureKatex().then(render => {
      render(div, {
        delimiters: KATEX_DELIMS,
        throwOnError: false,
        ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
      });
    }).catch(() => prettifyFallback(div));
  }
  // Fallback when KaTeX is unavailable: replace $$…$$, \[…\], \(…\) runs with the
  // best-effort Unicode rendering, skipping code so code samples are never rewritten.
  function prettifyFallback(div) {
    const rx = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)/g;
    const walker = document.createTreeWalker(div, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        for (let p = n.parentNode; p && p !== div; p = p.parentNode) {
          if (p.tagName === 'CODE' || p.tagName === 'PRE') return NodeFilter.FILTER_REJECT;
        }
        rx.lastIndex = 0;
        return rx.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    const targets = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) targets.push(n);
    targets.forEach(node => {
      const s = node.nodeValue, frag = document.createDocumentFragment();
      let last = 0, m; rx.lastIndex = 0;
      while ((m = rx.exec(s))) {
        if (m.index > last) frag.appendChild(document.createTextNode(s.slice(last, m.index)));
        const display = m[1] != null || m[2] != null;
        const tex = m[1] != null ? m[1] : (m[2] != null ? m[2] : m[3]);
        const span = document.createElement(display ? 'div' : 'span');
        span.className = P + '-math ' + (display ? P + '-math-block' : P + '-math-inline');
        span.textContent = prettifyMath(tex);
        frag.appendChild(span);
        last = rx.lastIndex;
      }
      if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)));
      node.parentNode.replaceChild(frag, node);
    });
  }

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
      // Prefer a real server (LLM) title; else the optimistic client title; else default.
      const serverTitle = (c.title && c.title !== 'New chat') ? c.title : '';
      title.textContent = serverTitle || state.localTitles[c.id] || 'New chat';
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
    delete state.localTitles[id];
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
  // Warm up KaTeX on first open so the first formula answer typesets without a
  // visible load delay (the promise is cached, so this runs at most once).
  function openPanel()  { panel.classList.add('open');    toggleBtn.style.display = 'none'; ensureKatex().catch(() => {}); }
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
  // Render a finished bot answer as formatted Markdown (vs. raw source text). User
  // messages and errors stay as plain text — only the model's answer is markup.
  function setBotHTML(div, text) {
    div.classList.add(P + '-md');
    div.innerHTML = renderMarkdown(text);
    typesetMath(div);                 // KaTeX (or Unicode fallback) for any formulas
  }
  function appendMessage(role, text, imgDataUrl, opts) {
    const div = document.createElement('div');
    const roleClass = role === 'user' ? CLS.user : role === 'error' ? CLS.error : CLS.bot;
    div.className = CLS.msg + ' ' + roleClass;
    if (role === 'error') div.setAttribute('role', 'alert');   // assertive announce
    // `opts.raw` keeps the bubble plain text — used for the live streaming preview,
    // which is re-rendered as Markdown once the authoritative final answer arrives.
    if (role === 'bot' && !(opts && opts.raw)) setBotHTML(div, text);
    else div.textContent = text;
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
    // Watchdog: abort a genuinely hung backend so the user gets a clear message
    // instead of an indefinite spinner. Generous, because grounded answers from a
    // CPU LLM can legitimately take a minute-plus; SSE keepalives hold the link.
    const controller = new AbortController();
    const watchdog = setTimeout(() => controller.abort(), 200000);  // 200s ceiling
    try {
      const body = { session_id: SESSION_ID, message: userText };
      if (state.activeConversationId) body.conversation_id = state.activeConversationId;
      if (att) { body.screenshot_base64 = att.base64; body.screenshot_mime = att.mime; }

      // Stream via SSE so the connection produces bytes throughout the long answer
      // (defeats the reverse-proxy idle timeout that surfaced as "something went
      // wrong"). streamChat renders the bot bubble itself and returns the
      // authoritative `final` payload (post output-guard).
      const data = await streamChat(body, typing, controller.signal);

      if (data.conversation_id) state.activeConversationId = data.conversation_id;
      // Authenticated + a brand-new conversation: show an instant optimistic title
      // (derived from the first message) so the row never reads "New chat", then
      // refresh a couple of times to pick up the background LLM-generated title.
      if (state.authed && wasNew && data.conversation_id) {
        state.localTitles[data.conversation_id] = deriveTitle(userText);
        if (!state.conversations.some(c => c.id === data.conversation_id)) {
          state.conversations.unshift({ id: data.conversation_id, title: '' });
        }
        renderConversations();
        setTimeout(loadConversations, 1500);   // pick up the LLM title when ready
        setTimeout(loadConversations, 4000);
      }
    } catch (err) {
      typing.remove();
      const timedOut = err && (err.name === 'AbortError' || controller.signal.aborted);
      appendMessage('error', timedOut
        ? 'The assistant is taking longer than usual. Please try again.'
        : 'Sorry, something went wrong. Please try again.');
      if (window.console) console.warn('[chat-widget]', err);
    } finally {
      clearTimeout(watchdog);
      setWaiting(false);
      clearAttachment();
    }
  }

  // ── Streaming transport (SSE) ────────────────────────────────────────────────
  // Parse one SSE frame ("event:"/"data:"/comment lines) into {event, data}.
  function parseSSEFrame(frame) {
    let event = 'message';
    const dataLines = [];
    for (const raw of frame.split('\n')) {
      const line = raw.replace(/\r$/, '');
      if (!line || line[0] === ':') continue;            // blank / ": keepalive" comment
      if (line.indexOf('event:') === 0) event = line.slice(6).trim();
      else if (line.indexOf('data:') === 0) dataLines.push(line.slice(5).replace(/^ /, ''));
    }
    if (!dataLines.length) return { event, data: null };
    try { return { event, data: JSON.parse(dataLines.join('\n')) }; }
    catch (e) { return { event, data: null }; }
  }

  // POST /chat/stream and consume the SSE stream. Renders tokens live for
  // responsiveness, then replaces them with the authoritative `final` answer
  // (which has passed the server output guard). Falls back to blocking /chat when
  // streaming is unavailable (older backend or no streaming body).
  async function streamChat(body, typing, signal) {
    let res;
    try {
      res = await fetch(cfg.apiBase + '/chat/stream', {
        method: 'POST',
        headers: await authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
        signal,
      });
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      return blockingChat(body, typing, signal);         // couldn't reach the stream
    }
    if (res.status === 404) return blockingChat(body, typing, signal);  // endpoint absent
    if (!res.ok || !res.body || !res.body.getReader) {
      if (!res.body || !res.body.getReader) return blockingChat(body, typing, signal);
      throw new Error('Server ' + res.status);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '', live = '', botDiv = null, final = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) !== -1) {
        const evt = parseSSEFrame(buf.slice(0, i));
        buf = buf.slice(i + 2);
        if (evt.event === 'token' && evt.data && evt.data.text != null) {
          if (!botDiv) { typing.remove(); botDiv = appendMessage('bot', '', null, { raw: true }); }
          live += evt.data.text;
          botDiv.textContent = live;                     // grow the live preview (plain)
          messages.scrollTop = messages.scrollHeight;
        } else if (evt.event === 'final' && evt.data) {
          final = evt.data;
        }
      }
    }
    if (!final) throw new Error('stream ended without a final event');
    // The final payload is authoritative — it may differ from the streamed tokens
    // (output guard can rewrite or refuse), so render it, not the preview.
    if (botDiv) setBotHTML(botDiv, final.answer);
    else { typing.remove(); appendMessage('bot', final.answer); }
    return final;
  }

  async function blockingChat(body, typing, signal) {
    const res = await fetch(cfg.apiBase + '/chat', {
      method: 'POST',
      headers: await authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok) throw new Error('Server ' + res.status);
    const data = await res.json();
    typing.remove();
    appendMessage('bot', data.answer);
    return data;
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
