// Prefer dated release snapshots; provider creation time is an explicit fallback.
export function modelTime(model) {
  const dated = model.id.match(/-(\d{4}-\d{2}-\d{2})$/);
  if (dated) {
    const parsed = Date.parse(dated[1] + 'T00:00:00Z');
    if (Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === dated[1]) return parsed;
  }
  return typeof model.created === 'number' && Number.isFinite(model.created) && model.created > 0 && model.created < 253402300800 ? model.created * 1000 : 0;
}

export function sortModels(models) {
  return [...models].sort((a, b) => {
    const time = modelTime(b) - modelTime(a);
    if (time) return time;
    const av = a.id.match(/^gpt-(\d+)(?:\.(\d+))?/);
    const bv = b.id.match(/^gpt-(\d+)(?:\.(\d+))?/);
    if (av && bv) {
      const version = Number(bv[1]) - Number(av[1]) || Number(bv[2] || 0) - Number(av[2] || 0);
      if (version) return version;
    }
    return a.id.localeCompare(b.id, 'en', { numeric: true });
  });
}
