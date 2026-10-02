/** Browser-local UI preferences, scoped to the HA user and machine entry. */
export function panelStateKey(hass, section, entryId = "") {
  const userId = hass?.user?.id;
  return typeof userId === "string" && userId
    ? `melitta-panel:v1:${JSON.stringify([userId, section, entryId])}`
    : null;
}

export function readPanelState(key) {
  if (!key) return null;
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

export function writePanelState(key, value) {
  if (!key) return;
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Disabled storage / quota exhaustion must leave the live controls usable.
  }
}
