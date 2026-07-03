// Provider credentials "add by type": unconfigured provider groups render hidden;
// choosing a type from the add-select reveals and opens that group. All groups
// stay in the DOM so their fields still submit (empty ones are harmless).
(function () {
  var select = document.querySelector("[data-add-provider]");
  if (!select) return;
  select.addEventListener("change", function () {
    var key = select.value;
    if (!key) return;
    var group = document.querySelector('[data-provider-group="' + key + '"]');
    if (group) {
      group.hidden = false;
      group.open = true;
      var opt = select.querySelector('option[value="' + key + '"]');
      if (opt) opt.remove();
      try {
        group.scrollIntoView({ behavior: "smooth", block: "nearest" });
        var firstInput = group.querySelector("input, textarea, select");
        if (firstInput) firstInput.focus();
      } catch (_) {}
    }
    select.value = "";
    // Hide the add row entirely once every provider has been surfaced.
    if (select.options.length <= 1) {
      var wrap = select.closest(".provider-add");
      if (wrap) wrap.hidden = true;
    }
  });
})();
