// Config sections as tabs: only the active section is visible; the sidebar
// jump-links act as tab buttons. Progressive enhancement — with JS off, every
// section stays visible (no `tabs-enabled` class is added). All sections remain
// in the DOM, so the single Save button still submits every field.
(function () {
  var layout = document.querySelector(".section-layout");
  if (!layout) return;
  var sections = Array.prototype.slice.call(layout.querySelectorAll(".section-card"));
  var links = Array.prototype.slice.call(layout.querySelectorAll(".jump-link"));
  if (sections.length < 2) return;

  function idOf(link) {
    var href = link.getAttribute("href") || "";
    return href.charAt(0) === "#" ? href.slice(1) : "";
  }

  function activate(id) {
    var match = sections.filter(function (s) {
      return s.id === id;
    });
    if (!match.length) id = sections[0].id;
    sections.forEach(function (s) {
      s.classList.toggle("is-active", s.id === id);
    });
    links.forEach(function (l) {
      l.classList.toggle("active", idOf(l) === id);
    });
    return id;
  }

  layout.classList.add("tabs-enabled");
  links.forEach(function (l) {
    l.addEventListener("click", function (e) {
      e.preventDefault();
      var id = activate(idOf(l));
      try {
        history.replaceState(null, "", "#" + id);
      } catch (_) {}
    });
  });

  var initial = (location.hash || "").replace(/^#/, "");
  activate(initial || sections[0].id);
})();
