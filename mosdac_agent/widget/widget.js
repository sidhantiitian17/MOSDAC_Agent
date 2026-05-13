/* MOSDAC chat widget — drops onto any HTML page via <iframe>.
 *
 * The host page can override window.MOSDAC_API to point at a different
 * backend URL prefix (e.g. staging vs. prod).
 */
(function () {
  const API = (window.MOSDAC_API || "/mosdac").replace(/\/$/, "");
  let sessionId = (window.crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

  const log  = document.getElementById("log");
  const form = document.getElementById("f");
  const msg  = document.getElementById("msg");

  function append(role, text) {
    const div = document.createElement("div");
    div.className = "msg " + (role === "user" ? "user" : "bot");
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function markErr(node, text) {
    node.classList.add("err");
    node.textContent = text;
  }

  fetch(API + "/config", { credentials: "include" })
    .then(r => r.ok ? r.json() : null)
    .then(cfg => {
      if (cfg && cfg.title) {
        document.title = cfg.title;
        const t = document.getElementById("title");
        if (t) t.textContent = cfg.title;
      }
    })
    .catch(() => {});

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = msg.value.trim();
    if (!text) return;
    append("user", text);
    msg.value = "";
    const pending = append("bot", "…");
    try {
      const r = await fetch(API + "/chat", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId })
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        markErr(pending, data.detail || ("HTTP " + r.status));
        return;
      }
      pending.textContent = data.answer || "(empty reply)";
      if (data.session_id) sessionId = data.session_id;
    } catch (err) {
      markErr(pending, "Network error: " + err.message);
    }
  });
})();
