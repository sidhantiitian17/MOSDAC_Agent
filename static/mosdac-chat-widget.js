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
    // {name} → the signed-in username (or "User" before sign-in). See renderGreeting().
    greeting:      "Hey {name}, what's on your mind today?",
    suggestions:   ['How can you help me browse?', 'What can you do?', 'Explain a topic'],
    elementPrefix: 'mosdac',
    // Where "Sign in" sends an anonymous user. Drupal's OpenID Connect login route
    // is the default; it signs the user in portal-wide and then exposes the token to
    // the page (meta tag below). The backend can override this via GET /config
    // (CHAT_API_LOGIN_URL). Set to '' to hide the Sign-in button.
    loginUrl:      '/user/login',
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
  tag.src = baseURL + 'graph-rag-chat-widget.js?v=2';   // bump ?v= when the widget changes (cache-bust)
  tag.async = false;
  document.head.appendChild(tag);
})();
