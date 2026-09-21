document.addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-info-toggle]");
  if (toggle) {
    const popover = toggle.parentElement.querySelector("[data-info-popover]");
    if (!popover) return;
    const isHidden = popover.hasAttribute("hidden");
    document.querySelectorAll("[data-info-popover]").forEach((p) => p.setAttribute("hidden", ""));
    document.querySelectorAll("[data-info-toggle]").forEach((t) => t.setAttribute("aria-expanded", "false"));
    if (isHidden) {
      popover.removeAttribute("hidden");
      toggle.setAttribute("aria-expanded", "true");
    }
    return;
  }
  if (!event.target.closest("[data-info-popover]")) {
    document.querySelectorAll("[data-info-popover]").forEach((p) => p.setAttribute("hidden", ""));
    document.querySelectorAll("[data-info-toggle]").forEach((t) => t.setAttribute("aria-expanded", "false"));
  }
});
