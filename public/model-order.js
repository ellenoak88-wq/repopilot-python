// Prefer snapshot dates and known release dates, then provider creation time.
export function modelTime(model) {
  const dated = model.id.match(/-(\d{4}-\d{2}-\d{2})$/);
  for (const date of [dated?.[1], model.releaseDate]) {
    if (!date) continue;
    const parsed = Date.parse(date + 'T00:00:00Z');
    if (Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === date) return parsed;
  }
  return typeof model.created === 'number' && Number.isFinite(model.created) && model.created > 0 && model.created < 253402300800 ? model.created * 1000 : 0;
}

export function isListedModel(id) {
  return !/image|realtime|live/i.test(id);
}

export function sortModels(models) {
  return models.filter(model => isListedModel(model.id)).sort((a, b) => {
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
