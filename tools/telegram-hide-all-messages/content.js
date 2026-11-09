(() => {
  "use strict";

  const HIDDEN_ATTRIBUTE = "data-code-anywhere-all-messages-hidden";
  const ENABLED_KEY = "codeAnywhereHideAllMessages";
  const LABELS = new Set(["All Messages", "所有消息", "全部消息"]);
  let scheduled = false;

  function enabled() {
    return localStorage.getItem(ENABLED_KEY) !== "0";
  }

  function restore() {
    document.querySelectorAll(`[${HIDDEN_ATTRIBUTE}]`).forEach((row) => {
      row.style.removeProperty("display");
      row.removeAttribute(HIDDEN_ATTRIBUTE);
    });
  }

  function isSidebarRow(element) {
    const rect = element.getBoundingClientRect();
    return rect.width >= 180
      && rect.height >= 38
      && rect.height <= 110
      && rect.left < window.innerWidth * 0.48;
  }

  function findRow(label) {
    let current = label;
    for (let depth = 0; current && depth < 9; depth += 1, current = current.parentElement) {
      if (isSidebarRow(current)) {
        return current;
      }
    }
    return null;
  }

  function hide() {
    if (!enabled()) {
      restore();
      return;
    }

    document.querySelectorAll("span, div, p").forEach((element) => {
      if (!LABELS.has((element.textContent || "").trim())) {
        return;
      }
      const row = findRow(element);
      if (!row || row.hasAttribute(HIDDEN_ATTRIBUTE)) {
        return;
      }
      row.setAttribute(HIDDEN_ATTRIBUTE, "true");
      row.style.setProperty("display", "none", "important");
    });
  }

  function schedule() {
    if (scheduled) {
      return;
    }
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      hide();
    });
  }

  new MutationObserver(schedule).observe(document.documentElement, {
    childList: true,
    subtree: true
  });

  // Alt/Option + Shift + A temporarily toggles the row for troubleshooting.
  window.addEventListener("keydown", (event) => {
    if (!event.altKey || !event.shiftKey || event.code !== "KeyA") {
      return;
    }
    const next = enabled() ? "0" : "1";
    localStorage.setItem(ENABLED_KEY, next);
    if (next === "0") {
      restore();
    } else {
      schedule();
    }
  });

  schedule();
})();
