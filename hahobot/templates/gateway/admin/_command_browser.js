(() => {
  const root = document.querySelector("[data-command-browser]");
  if (!root) return;
  const items = Array.from(root.querySelectorAll("[data-command-target]"));
  const panels = new Map(
    Array.from(root.querySelectorAll("[data-command-panel]")).map((panel) => [
      panel.dataset.commandPanel,
      panel,
    ]),
  );

  const select = (id, updateHash = false) => {
    items.forEach((item) => {
      const active = item.dataset.commandTarget === id;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    panels.forEach((panel, panelId) => {
      const active = panelId === id;
      panel.classList.toggle("active", active);
      panel.hidden = !active;
      panel.setAttribute("aria-hidden", String(!active));
    });
    if (updateHash && window.location.hash !== "#" + id) {
      history.replaceState(null, "", "#" + id);
    }
  };

  const initialId = (() => {
    const hash = window.location.hash.replace(/^#/, "");
    if (hash && panels.has(hash)) return hash;
    const first = items[0];
    return first ? first.dataset.commandTarget : null;
  })();

  if (initialId) select(initialId);

  items.forEach((item) => {
    item.addEventListener("click", (event) => {
      event.preventDefault();
      const id = item.dataset.commandTarget;
      if (id) select(id, true);
    });
  });

  window.addEventListener("hashchange", () => {
    const hash = window.location.hash.replace(/^#/, "");
    if (hash && panels.has(hash)) select(hash);
  });
})();
