// static/mosdac-chat-widget.js
// MOSDAC-branded shim — sets ISRO/MOSDAC BOT defaults, then loads the generic widget.
// Kept for backward compatibility with existing NGINX sub_filter rules and portal
// <script> tags. For a new portal, point directly at /static/graph-rag-chat-widget.js
// with your own GRAPH_RAG_CHAT_CONFIG. Anything set on the page before this script
// loads overrides these defaults (page config wins), so re-branding is config-only.

(function () {
  'use strict';

  window.GRAPH_RAG_CHAT_CONFIG = Object.assign({
    apiBase:       '/chatapi',
    botTitle:      'MOSDAC BOT',
    // ISRO logo — override per deployment with whatever the site serves.
    logoUrl:       '/static/isro-logo.png',
    greeting:      "Hey User, what's on your mind today?",
    suggestions:   ['How can you help me browse?', 'What can you do?', 'Explain a topic'],
    elementPrefix: 'mosdac',
    // Default SSO token source: a token injected server-side by Drupal/OIDC into a
    // JS variable or <meta name="kc-token">. No token → anonymous, ephemeral chat.
    getToken: function () {
      try {
        if (window.KC_TOKEN) return window.KC_TOKEN;
        var m = document.querySelector('meta[name="kc-token"]');
        return (m && m.content) || '';
      } catch (e) { return ''; }
    },
  }, window.GRAPH_RAG_CHAT_CONFIG || {});

  // Load the generic widget from the same folder this shim was served from.
  const here = document.currentScript ? document.currentScript.src : '';
  const baseURL = here.replace(/mosdac-chat-widget\.js.*$/, '');
  const tag = document.createElement('script');
  tag.src = baseURL + 'graph-rag-chat-widget.js';
  tag.async = false;
  document.head.appendChild(tag);
})();
