(function () {
  "use strict";

  // Expert mode is opt-in: either ?expert or ?expert=1 enables it.
  const PARAMETER = "expert";
  const NAV_REVISION_PARAMETER = "ui_rev";
  const NAV_REVISION = "20260524b";
  const rawValue = new URLSearchParams(window.location.search).get(PARAMETER);
  const disabledValues = new Set(["0", "false", "off", "no"]);
  const isExpert = rawValue !== null && !disabledValues.has(rawValue.toLowerCase());
  const mode = isExpert ? "expert" : "simple";
  const html = document.documentElement;

  html.dataset.uiMode = mode;
  html.classList.toggle("expert-mode", isExpert);

  function decorateLink(anchor) {
    if (anchor.hasAttribute("download")) return;

    const url = new URL(anchor.href, window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.endsWith(".html")) return;

    url.searchParams.set(NAV_REVISION_PARAMETER, NAV_REVISION);
    if (isExpert) {
      url.searchParams.set(PARAMETER, "1");
    } else {
      url.searchParams.delete(PARAMETER);
    }
    anchor.href = url.href;
  }

  function carryToLinks(container) {
    if (container.matches && container.matches("a[href]")) decorateLink(container);
    if (container.querySelectorAll) {
      container.querySelectorAll("a[href]").forEach(decorateLink);
    }
  }

  window.RFUiMode = Object.freeze({
    isExpert,
    mode,
    parameter: PARAMETER,
    navRevision: NAV_REVISION,
    carryToLinks,
  });

  document.addEventListener("DOMContentLoaded", function () {
    document.body.dataset.uiMode = mode;
    carryToLinks(document);

    // Some page navigation is rebuilt after asynchronous option loads.
    new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(function (node) {
          if (node.nodeType === Node.ELEMENT_NODE) carryToLinks(node);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  });
})();
