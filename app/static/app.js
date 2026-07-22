// Light progressive enhancement for the control panel.
// Auto-refresh the dashboard so run status/counters update without a manual reload.
(function () {
  if (window.location.pathname === "/") {
    let hidden = false;
    document.addEventListener("visibilitychange", () => { hidden = document.hidden; });
    setInterval(() => {
      if (!hidden) window.location.reload();
    }, 20000);
  }
})();
