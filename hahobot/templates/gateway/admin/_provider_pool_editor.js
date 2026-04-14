(() => {
  const editors = Array.from(document.querySelectorAll("[data-provider-pool-editor]"));
  if (!editors.length) return;

  const createRow = (editor) => {
    const template = editor.querySelector("[data-provider-pool-template]");
    if (!template) return null;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = template.innerHTML.trim();
    return wrapper.firstElementChild;
  };

  const ensureRow = (editor) => {
    const rows = editor.querySelector("[data-provider-pool-rows]");
    if (!rows) return;
    if (!rows.querySelector("[data-provider-pool-row]")) {
      const row = createRow(editor);
      if (row) rows.appendChild(row);
    }
  };

  editors.forEach((editor) => {
    ensureRow(editor);
    editor.addEventListener("click", (event) => {
      const row = event.target.closest("[data-provider-pool-row]");
      const rows = editor.querySelector("[data-provider-pool-rows]");

      const addButton = event.target.closest("[data-provider-pool-add]");
      if (addButton) {
        const row = createRow(editor);
        if (rows && row) rows.appendChild(row);
        return;
      }

      const moveUpButton = event.target.closest("[data-provider-pool-move-up]");
      if (moveUpButton && rows && row) {
        const previous = row.previousElementSibling;
        if (previous) rows.insertBefore(row, previous);
        return;
      }

      const moveDownButton = event.target.closest("[data-provider-pool-move-down]");
      if (moveDownButton && rows && row) {
        const next = row.nextElementSibling;
        if (next) rows.insertBefore(next, row);
        return;
      }

      const removeButton = event.target.closest("[data-provider-pool-remove]");
      if (!removeButton) return;
      if (row) row.remove();
      ensureRow(editor);
    });
  });
})();
