// Ładowany przed CSS, aby zastosować motyw przed pierwszym wyświetleniem strony.
(() => {
  const key = "wikihum-chat-theme";
  let preference;
  let media;
  try { preference = window.localStorage.getItem(key); } catch { /* Pamięć może być niedostępna. */ }
  if (!["light", "dark"].includes(preference)) preference = null;
  try { media = window.matchMedia("(prefers-color-scheme: dark)"); } catch { /* Domyślnie jasny. */ }

  function apply() {
    const theme = preference || (media?.matches ? "dark" : "light");
    document.documentElement.dataset.theme = theme;
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    const label = document.documentElement.lang === "en"
      ? (theme === "dark" ? "Switch to light mode" : "Switch to dark mode")
      : (theme === "dark" ? "Włącz tryb dzienny" : "Włącz tryb nocny");
    button.setAttribute("aria-label", label);
    button.title = label;
    for (const icon of button.querySelectorAll("[data-theme-icon]")) {
      icon.toggleAttribute("hidden", icon.dataset.themeIcon !== (theme === "dark" ? "sun" : "moon"));
    }
    button.hidden = false;
  }

  function mount() {
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    button.addEventListener("click", () => {
      preference = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      try { window.localStorage.setItem(key, preference); } catch { /* Przełączanie nadal działa. */ }
      apply();
    });
    apply();
  }

  apply();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once: true });
  else mount();
  media?.addEventListener?.("change", apply);
  window.addEventListener("languagechange", apply);
  window.addEventListener("storage", event => {
    if (event.key !== key && event.key !== null) return;
    preference = ["light", "dark"].includes(event.newValue) ? event.newValue : null;
    apply();
  });
})();
