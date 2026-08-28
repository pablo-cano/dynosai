(() => {
  const storageKey = "dynosai.studio.theme";
  const theme = localStorage.getItem(storageKey) || "system";
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
  root.dataset.themePreference = theme;
})();
