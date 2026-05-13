// static/graph-rag-chat-widget.js
// Generic Graph RAG Chatbot Widget — domain-agnostic.
// Inject this script into any portal page. Branding comes from runtime config.
//
// Customize per-domain by setting window.GRAPH_RAG_CHAT_CONFIG before this script loads:
//
//   <script>
//     window.GRAPH_RAG_CHAT_CONFIG = {
//       apiBase:   '/chatapi',                // backend prefix (default: '/chatapi')
//       title:     'MOSDAC Assistant',        // panel title (default: from /config endpoint)
//       botLogo:   '/favicon.ico',            // logo URL
//       greeting:  'Hello! Ask me anything.', // initial bot message
//       accent:    '#1565c0',                 // primary brand colour
//       panelBg:   '#1a1a2e',                 // panel background
//       elementPrefix: 'grag',                // CSS id prefix (avoids collisions)
//     };
//   </script>
//   <script src="/static/graph-rag-chat-widget.js"></script>

(function () {
  'use strict';

  // ── Defaults ─────────────────────────────────────────────────────────────
  const DEFAULTS = {
    apiBase:       '/chatapi',
    title:         'Assistant',
    botLogo:       '',
    greeting:      'Hello! How can I help you today?',
    accent:        '#1565c0',
    accentHover:   '#0d47a1',
    panelBg:       '#1a1a2e',
    headerBg:      '#0d1b4b',
    msgBotBg:      '#263054',
    msgUserBg:     '#1565c0',
    textColor:     '#e0e0e0',
    placeholderColor: '#7986cb',
    elementPrefix: 'grag',
    panelWidth:    420,
    enableScreenshot: true,
    fetchRemoteConfig: true,
  };

  const cfg = Object.assign({}, DEFAULTS, window.GRAPH_RAG_CHAT_CONFIG || {});
  const P = cfg.elementPrefix;
  const SESSION_KEY = P + '_chat_sid';

  const SESSION_ID = sessionStorage.getItem(SESSION_KEY) || (() => {
    const id = 'sess_' + Math.random().toString(36).slice(2);
    sessionStorage.setItem(SESSION_KEY, id);
    return id;
  })();

  const ID = {
    toggle:   `${P}-chat-toggle`,
    panel:    `${P}-chat-panel`,
    header:   `${P}-chat-header`,
    close:    `${P}-chat-close`,
    messages: `${P}-chat-messages`,
    input:    `${P}-chat-input`,
    inputRow: `${P}-chat-input-row`,
    sendBtn:  `${P}-btn-send`,
    ssBtn:    `${P}-btn-screenshot`,
    preview:  `${P}-attach-preview`,
    previewImg: `${P}-attach-img`,
    removeBtn:`${P}-remove-attach`,
  };
  const CLS = {
    msg:      `${P}-msg`,
    user:     `${P}-msg-user`,
    bot:      `${P}-msg-bot`,
    error:    `${P}-msg-error`,
    typing:   `${P}-typing`,
    thumb:    `${P}-thumb`,
    iconBtn:  `${P}-icon-btn`,
  };

  function injectCSS() {
    const style = document.createElement('style');
    style.textContent = `
      #${ID.toggle} {
        position: fixed; bottom: 28px; right: 28px; z-index: 9998;
        width: 54px; height: 54px; border-radius: 50%;
        background: ${cfg.accent}; border: none; cursor: pointer;
        box-shadow: 0 4px 14px rgba(0,0,0,0.35);
        display: flex; align-items: center; justify-content: center;
        transition: background 0.2s;
      }
      #${ID.toggle}:hover { background: ${cfg.accentHover}; }
      #${ID.toggle} svg { width: 26px; height: 26px; fill: #fff; }

      #${ID.panel} {
        position: fixed; top: 0; right: -${cfg.panelWidth + 20}px;
        width: ${cfg.panelWidth}px; height: 100vh;
        background: ${cfg.panelBg}; color: ${cfg.textColor};
        box-shadow: -4px 0 20px rgba(0,0,0,0.5);
        display: flex; flex-direction: column; z-index: 9999;
        transition: right 0.3s ease; font-family: 'Segoe UI', sans-serif;
        border-left: 2px solid ${cfg.accent};
      }
      #${ID.panel}.open { right: 0; }

      #${ID.header} {
        background: ${cfg.headerBg}; padding: 14px 16px;
        display: flex; align-items: center; gap: 10px;
        border-bottom: 1px solid ${cfg.accent};
      }
      #${ID.header} img { width: 28px; height: 28px; border-radius: 4px; }
      #${ID.header} span { font-weight: 600; font-size: 15px; flex: 1; }
      #${ID.close} {
        background: none; border: none; color: #90caf9; font-size: 20px;
        cursor: pointer; padding: 0 4px; line-height: 1;
      }
      #${ID.close}:hover { color: #fff; }

      #${ID.messages} {
        flex: 1; overflow-y: auto; padding: 14px;
        display: flex; flex-direction: column; gap: 12px;
      }
      .${CLS.msg} {
        max-width: 88%; padding: 10px 14px; border-radius: 12px;
        font-size: 13.5px; line-height: 1.55; word-break: break-word;
      }
      .${CLS.user} {
        align-self: flex-end; background: ${cfg.msgUserBg}; color: #fff;
        border-bottom-right-radius: 3px;
      }
      .${CLS.bot} {
        align-self: flex-start; background: ${cfg.msgBotBg}; color: ${cfg.textColor};
        border-bottom-left-radius: 3px; white-space: pre-wrap;
      }
      .${CLS.error} { background: #4a1010; color: #ff8a80; }
      .${CLS.thumb} {
        max-width: 100%; border-radius: 6px; margin-top: 6px;
        display: block; border: 1px solid ${cfg.accent};
      }
      .${CLS.typing} { color: #90caf9; font-style: italic; font-size: 12px; }

      #${ID.preview} {
        margin: 0 14px; padding: 8px 10px;
        background: ${cfg.msgBotBg}; border-radius: 8px; font-size: 12px;
        display: none; align-items: center; gap: 8px;
      }
      #${ID.preview} img { height: 44px; border-radius: 4px; }
      #${ID.removeBtn} { background: none; border: none; color: #f44336;
        font-size: 16px; cursor: pointer; margin-left: auto; }

      #${ID.inputRow} {
        padding: 10px 12px; border-top: 1px solid ${cfg.msgBotBg};
        display: flex; gap: 8px; align-items: flex-end;
      }
      #${ID.input} {
        flex: 1; background: ${cfg.msgBotBg}; border: 1px solid ${cfg.accent};
        color: ${cfg.textColor}; border-radius: 8px; padding: 9px 12px;
        font-size: 13.5px; resize: none; outline: none;
        min-height: 38px; max-height: 120px; overflow-y: auto;
      }
      #${ID.input}::placeholder { color: ${cfg.placeholderColor}; }
      .${CLS.iconBtn} {
        background: ${cfg.accent}; border: none; border-radius: 8px;
        width: 38px; height: 38px; cursor: pointer; flex-shrink: 0;
        display: flex; align-items: center; justify-content: center;
        color: #fff; transition: background 0.2s;
      }
      .${CLS.iconBtn}:hover { background: ${cfg.accentHover}; }
      .${CLS.iconBtn} svg { width: 18px; height: 18px; fill: currentColor; }
      .${CLS.iconBtn}:disabled { opacity: 0.4; cursor: not-allowed; }
    `;
    document.head.appendChild(style);
  }

  function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function buildDOM() {
    const screenshotBtnHTML = cfg.enableScreenshot ? `
      <button class="${CLS.iconBtn}" id="${ID.ssBtn}" title="Take screenshot of current page">
        <svg viewBox="0 0 24 24"><path d="M9 3L7.17 5H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2h-3.17L15 3H9zm3 15c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z"/><circle cx="12" cy="13" r="2.5" fill="currentColor" opacity=".6"/></svg>
      </button>` : '';

    const logoHTML = cfg.botLogo
      ? `<img src="${cfg.botLogo}" alt="logo" onerror="this.style.display='none'">`
      : '';

    document.body.insertAdjacentHTML('beforeend', `
      <button id="${ID.toggle}" title="Open ${escapeHTML(cfg.title)}">
        <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
      </button>

      <div id="${ID.panel}">
        <div id="${ID.header}">
          ${logoHTML}
          <span data-role="title">${escapeHTML(cfg.title)}</span>
          <button id="${ID.close}" title="Close">&#x2715;</button>
        </div>

        <div id="${ID.messages}">
          <div class="${CLS.msg} ${CLS.bot}" data-role="greeting">${escapeHTML(cfg.greeting)}</div>
        </div>

        <div id="${ID.preview}">
          <img id="${ID.previewImg}" src="" alt="screenshot">
          <span>Screenshot attached</span>
          <button id="${ID.removeBtn}" title="Remove">&#x2715;</button>
        </div>

        <div id="${ID.inputRow}">
          ${screenshotBtnHTML}
          <textarea id="${ID.input}" placeholder="Type your question…" rows="1"></textarea>
          <button class="${CLS.iconBtn}" id="${ID.sendBtn}" title="Send">
            <svg viewBox="0 0 24 24"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg>
          </button>
        </div>
      </div>
    `);
  }

  injectCSS();
  buildDOM();

  let attachedScreenshot = null;
  let isWaiting = false;

  const panel      = document.getElementById(ID.panel);
  const toggleBtn  = document.getElementById(ID.toggle);
  const closeBtn   = document.getElementById(ID.close);
  const messages   = document.getElementById(ID.messages);
  const input      = document.getElementById(ID.input);
  const sendBtn    = document.getElementById(ID.sendBtn);
  const ssBtn      = document.getElementById(ID.ssBtn);
  const preview    = document.getElementById(ID.preview);
  const previewImg = document.getElementById(ID.previewImg);
  const removeBtn  = document.getElementById(ID.removeBtn);
  const titleEl    = panel.querySelector('[data-role="title"]');

  if (cfg.fetchRemoteConfig) {
    fetch(cfg.apiBase + '/config', { method: 'GET' })
      .then(r => r.ok ? r.json() : null)
      .then(remote => {
        if (!remote) return;
        const explicit = window.GRAPH_RAG_CHAT_CONFIG || {};
        if (!explicit.title && remote.title) titleEl.textContent = remote.title;
        if (remote.screenshot_enabled === false && ssBtn) {
          ssBtn.style.display = 'none';
        }
      })
      .catch(() => {});
  }

  toggleBtn.addEventListener('click', () => panel.classList.add('open'));
  closeBtn.addEventListener('click',  () => panel.classList.remove('open'));

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  sendBtn.addEventListener('click', sendMessage);

  if (ssBtn) ssBtn.addEventListener('click', takeScreenshot);
  if (removeBtn) removeBtn.addEventListener('click', clearAttachment);

  function appendMessage(role, text, imgDataUrl) {
    const div = document.createElement('div');
    const roleClass = role === 'user' ? CLS.user
                     : role === 'error' ? CLS.error
                     : CLS.bot;
    div.className = CLS.msg + ' ' + roleClass;
    div.textContent = text;
    if (imgDataUrl) {
      const img = document.createElement('img');
      img.className = CLS.thumb;
      img.src = imgDataUrl;
      div.appendChild(img);
    }
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  function setWaiting(state) {
    isWaiting = state;
    sendBtn.disabled = state;
    if (ssBtn) ssBtn.disabled = state;
    input.disabled = state;
  }

  function clearAttachment() {
    attachedScreenshot = null;
    preview.style.display = 'none';
    previewImg.src = '';
  }

  async function sendMessage() {
    if (isWaiting) return;
    const text = input.value.trim();
    if (!text && !attachedScreenshot) return;

    const userText    = text || '(screenshot attached — please analyse)';
    const displayUrl  = attachedScreenshot
      ? 'data:' + attachedScreenshot.mime + ';base64,' + attachedScreenshot.base64
      : null;

    appendMessage('user', userText, displayUrl);
    input.value = '';
    input.style.height = 'auto';

    const typing = appendMessage('bot', 'Thinking…', null);
    typing.classList.add(CLS.typing);
    setWaiting(true);

    try {
      const body = { session_id: SESSION_ID, message: userText };
      if (attachedScreenshot) {
        body.screenshot_base64 = attachedScreenshot.base64;
        body.screenshot_mime   = attachedScreenshot.mime;
      }
      const res = await fetch(cfg.apiBase + '/chat', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
      });
      if (!res.ok) throw new Error('Server ' + res.status + ': ' + await res.text());
      const data = await res.json();
      typing.remove();
      appendMessage('bot', data.answer);
    } catch (err) {
      typing.remove();
      appendMessage('error', 'Error: ' + err.message);
    } finally {
      setWaiting(false);
      clearAttachment();
    }
  }

  async function takeScreenshot() {
    if (isWaiting) return;

    if (window.html2canvas) {
      try {
        panel.style.display = 'none';
        const canvas = await html2canvas(document.body, {
          useCORS: true, allowTaint: true, scale: 1, logging: false,
        });
        panel.style.display = 'flex';
        storeScreenshot(canvas.toDataURL('image/png'));
        return;
      } catch (e) {
        panel.style.display = 'flex';
        console.warn('html2canvas failed, trying Screen Capture API', e);
      }
    }

    if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
      try {
        const stream  = await navigator.mediaDevices.getDisplayMedia({ video: true });
        const track   = stream.getVideoTracks()[0];
        const capture = new ImageCapture(track);
        const bitmap  = await capture.grabFrame();
        track.stop();
        const canvas  = document.createElement('canvas');
        canvas.width  = bitmap.width;
        canvas.height = bitmap.height;
        canvas.getContext('2d').drawImage(bitmap, 0, 0);
        storeScreenshot(canvas.toDataURL('image/png'));
      } catch (e) {
        alert('Screenshot permission denied. You can also paste a screenshot with Ctrl+V.');
      }
      return;
    }
    alert('Screenshot not available in this browser. Paste an image with Ctrl+V instead.');
  }

  function storeScreenshot(dataUrl) {
    const [header, base64] = dataUrl.split(',');
    const mime = header.replace('data:', '').replace(';base64', '');
    attachedScreenshot = { base64, mime };
    previewImg.src     = dataUrl;
    preview.style.display = 'flex';
  }

  input.addEventListener('paste', e => {
    for (const item of e.clipboardData.items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const blob   = item.getAsFile();
        const reader = new FileReader();
        reader.onload = ev => storeScreenshot(ev.target.result);
        reader.readAsDataURL(blob);
        break;
      }
    }
  });

  window.GraphRagChat = {
    open:  () => panel.classList.add('open'),
    close: () => panel.classList.remove('open'),
    send:  (text) => { input.value = text; sendMessage(); },
    config: cfg,
  };
})();
