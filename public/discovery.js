const $ = id => document.getElementById(id);
const node = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const count = value => Number.isInteger(value) ? value.toLocaleString('zh-CN') : '未知';

export function createDiscovery(onImport) {
  let data = null, imported = new Map(), busy = false, loading = false, controller, version = 0;
  const controls = ['discover-source', 'discover-language', 'discover-since', 'discover-topic'];
  function sync() {
    const popular = $('discover-source').value === 'popular';
    $('discover-period-field').classList.toggle('hidden', popular);
    $('discover-topic-field').classList.toggle('hidden', !popular);
    controls.forEach(id => $(id).disabled = busy);
    $('discover-refresh').disabled = busy || loading;
    $('discover-refresh').textContent = loading ? '读取中…' : '刷新推荐';
    $('discover-cards').setAttribute('aria-busy', String(loading));
    $('discover-cards').querySelectorAll('button').forEach(button => button.disabled = busy || loading);
  }
  function renderCards() {
    $('discover-cards').replaceChildren(...(data?.items || []).map(item => {
      const card = node('article', 'discover-card');
      const reason = data.source === 'trending' ? `TRENDING · #${item.rank}` : `热门项目 · #${item.rank}`;
      card.append(node('div', 'discover-rank', reason), node('h2', '', item.id), node('p', 'discover-description', item.description || '该仓库暂未提供简介。'));
      const stats = node('div', 'discover-stats');
      [item.language || '未标注语言', `★ ${count(item.stars)}`, `⑂ ${count(item.forks)}`].forEach(text => stats.append(node('span', '', text)));
      card.append(stats);
      if (data.source === 'trending') {
        const period = {daily: '今日', weekly: '本周', monthly: '本月'}[data.since];
        card.append(node('p', 'discover-growth', item.periodStars === null ? '榜单未提供新增 Star 数' : `${period}新增 ${count(item.periodStars)} Star`));
      }
      const actions = node('div', 'discover-actions');
      const link = node('a', '', '在 GitHub 查看 ↗');
      link.href = item.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      const existing = imported.get(item.id.toLowerCase()) || item.importedId;
      const button = node('button', existing ? 'secondary' : 'primary', existing ? '已导入 · 打开' : '导入并探索');
      button.type = 'button'; button.setAttribute('aria-label', `${existing ? '打开' : '导入'} ${item.id}`);
      button.onclick = () => { if (!busy && !loading) onImport(item.url); };
      actions.append(link, button); card.append(actions); return card;
    }));
    sync();
  }
  async function load(refresh = false) {
    const revision = ++version;
    controller?.abort(); controller = new AbortController();
    const source = $('discover-source').value;
    const params = new URLSearchParams({source, language: $('discover-language').value,
      since: source === 'trending' ? $('discover-since').value : 'daily',
      topic: source === 'popular' ? $('discover-topic').value : '', refresh: refresh ? '1' : '0'});
    loading = true; data = null; renderCards();
    $('discover-status').textContent = '正在读取 GitHub 项目…';
    $('discover-warning').classList.add('hidden'); $('discover-source-link').classList.add('hidden');
    $('discover-rule').textContent = source === 'trending'
      ? '按 GitHub Trending 原榜单排序，可查看每日、每周或每月趋势。'
      : '公开、非归档、非 Fork，至少 1,000 Star，近 180 天有推送；按 Star 总数降序展示前 30 个。';
    try {
      const response = await fetch(`/api/discover?${params}`, {signal: controller.signal});
      const result = await response.json();
      if (!response.ok) throw Error(result.error || '推荐加载失败，请重试。');
      if (revision !== version) return;
      data = result;
      const time = new Date(data.fetchedAt).toLocaleString('zh-CN', {hour12: false});
      $('discover-status').textContent = `${data.items.length ? `${data.items.length} 个项目` : '此筛选条件暂无项目'} · ${data.stale ? '历史缓存' : data.cached ? '缓存结果' : '已更新'} · 数据时间 ${time}`;
      const warnings = [data.stale ? '更新未完成，当前显示上次成功获取的数据。' : '', data.warning, data.partial ? 'GitHub 搜索未返回完整结果，可稍后刷新。' : ''].filter(Boolean);
      $('discover-warning').textContent = warnings.join(' ');
      $('discover-warning').classList.toggle('hidden', !warnings.length);
      $('discover-source-link').href = data.sourceUrl;
      $('discover-source-link').classList.remove('hidden');
      renderCards();
    } catch (error) {
      if (revision === version && error.name !== 'AbortError') $('discover-status').textContent = error.message;
    } finally {
      if (revision === version) { loading = false; sync(); }
    }
  }
  controls.forEach(id => $(id).onchange = () => load());
  $('discover-refresh').onclick = () => load(true);
  sync();
  return {
    show() { if (!data && !loading) load(); },
    setBusy(value) { busy = value; sync(); },
    setImported(repos) { imported = new Map(repos.map(repo => [repo.id.toLowerCase(), repo.id])); renderCards(); },
  };
}
