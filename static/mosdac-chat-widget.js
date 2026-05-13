// static/mosdac-chat-widget.js
// MOSDAC-specific shim — sets MOSDAC branding then loads the generic widget.
// Kept for backward compatibility with existing NGINX sub_filter rules and
// portal <script> tags. For any new portal, point directly at
// /static/graph-rag-chat-widget.js with your own GRAPH_RAG_CHAT_CONFIG.

(function () {
  'use strict';

  window.GRAPH_RAG_CHAT_CONFIG = Object.assign({
    apiBase:       '/chatapi',
    title:         'MOSDAC Assistant',
    botLogo:       '/favicon.ico',
    greeting:      'Hello! I am the MOSDAC Assistant. Ask me anything about satellite data, products, cyclones, ocean state, or click the camera icon to attach a screenshot of what you see.',
    accent:        '#1565c0',
    accentHover:   '#0d47a1',
    panelBg:       '#1a1a2e',
    headerBg:      '#0d1b4b',
    elementPrefix: 'mosdac',
  }, window.GRAPH_RAG_CHAT_CONFIG || {});

  // Resolve the script path so we can load the generic widget from the same folder.
  const here = document.currentScript ? document.currentScript.src : '';
  const baseURL = here.replace(/mosdac-chat-widget\.js.*$/, '');
  const tag = document.createElement('script');
  tag.src = baseURL + 'graph-rag-chat-widget.js';
  tag.async = false;
  document.head.appendChild(tag);
})();
