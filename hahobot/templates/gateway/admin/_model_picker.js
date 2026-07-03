// Model picker: fetch a provider's /models list into a datalist while keeping
// the field free-text (operator can still type any model id by hand).
(function () {
  document.querySelectorAll("[data-model-field]").forEach(function (wrap) {
    var input = wrap.querySelector('input[list]');
    var datalist = wrap.querySelector("datalist");
    var button = wrap.querySelector("[data-fetch-models]");
    var status = wrap.querySelector("[data-model-status]");
    if (!input || !datalist || !button) return;

    button.addEventListener("click", function () {
      // Prefer the picker's own provider dropdown (lets you browse e.g.
      // OpenRouter without changing agents.defaults.provider); fall back to the
      // provider config field.
      var providerSelect = wrap.querySelector("[data-model-provider]");
      var provider = "";
      if (providerSelect && providerSelect.value) {
        provider = providerSelect.value.trim();
      } else {
        var providerField = wrap.dataset.providerField;
        var providerInput = providerField
          ? document.querySelector('[name="' + providerField + '"]')
          : null;
        provider = providerInput ? providerInput.value.trim() : "";
      }

      button.disabled = true;
      if (status) status.textContent = wrap.dataset.loadingText || "…";

      fetch("/admin/config/models?provider=" + encodeURIComponent(provider), {
        headers: { Accept: "application/json" },
      })
        .then(function (resp) {
          return resp.json().then(function (body) {
            return { ok: resp.ok, body: body };
          });
        })
        .then(function (res) {
          if (!res.ok) {
            var msg = (res.body && res.body.error) || wrap.dataset.errorText || "error";
            if (status) status.textContent = msg;
            return;
          }
          var models = (res.body && res.body.models) || [];
          datalist.innerHTML = "";
          models.forEach(function (id) {
            var opt = document.createElement("option");
            opt.value = id;
            datalist.appendChild(opt);
          });
          if (status) {
            status.textContent = (wrap.dataset.countText || "{n}").replace(
              "{n}",
              String(models.length)
            );
          }
          // Surface the suggestion list immediately.
          try {
            input.focus();
          } catch (e) {}
        })
        .catch(function () {
          if (status) status.textContent = wrap.dataset.errorText || "error";
        })
        .finally(function () {
          button.disabled = false;
        });
    });
  });
})();
