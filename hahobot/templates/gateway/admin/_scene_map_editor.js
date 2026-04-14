(() => {
  const editors = Array.from(document.querySelectorAll("[data-scene-map-editor]"));
  if (!editors.length) return;

  const createRow = (editor) => {
    const template = editor.querySelector("[data-scene-map-template]");
    if (!template) return null;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = template.innerHTML.trim();
    return wrapper.firstElementChild;
  };

  const ensureRow = (editor) => {
    const rows = editor.querySelector("[data-scene-map-rows]");
    if (!rows) return;
    if (!rows.querySelector("[data-scene-map-row]")) {
      const row = createRow(editor);
      if (row) rows.appendChild(row);
    }
  };

  editors.forEach((editor) => {
    ensureRow(editor);
    editor.addEventListener("click", (event) => {
      const row = event.target.closest("[data-scene-map-row]");
      const rows = editor.querySelector("[data-scene-map-rows]");

      const addButton = event.target.closest("[data-scene-map-add]");
      if (addButton) {
        const nextRow = createRow(editor);
        if (rows && nextRow) rows.appendChild(nextRow);
        return;
      }

      const moveUpButton = event.target.closest("[data-scene-map-move-up]");
      if (moveUpButton && rows && row) {
        const previous = row.previousElementSibling;
        if (previous) rows.insertBefore(row, previous);
        return;
      }

      const moveDownButton = event.target.closest("[data-scene-map-move-down]");
      if (moveDownButton && rows && row) {
        const next = row.nextElementSibling;
        if (next) rows.insertBefore(next, row);
        return;
      }

      const removeButton = event.target.closest("[data-scene-map-remove]");
      if (!removeButton) return;
      if (row) row.remove();
      ensureRow(editor);
    });
  });
})();
