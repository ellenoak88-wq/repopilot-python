// Repository navigation and read-only search. Source text is always rendered as text.
export function createExplorer(openFile) {
  const $ = id => document.getElementById(id);
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  let repo = null, files = [], controller = null, generation = 0;
  const expanded = new Map();

  function selectTab(name, focus = false) {
    for (const tab of ['files', 'search']) {
      const selected = tab === name;
      $(`explorer-tab-${tab}`).setAttribute('aria-selected', String(selected));
      $(`explorer-tab-${tab}`).tabIndex = selected ? 0 : -1;
      $(`explorer-panel-${tab}`).classList.toggle('hidden', !selected);
    }
    if (focus) $(`explorer-tab-${name}`).focus();
  }
  for (const tab of ['files', 'search']) {
    $(`explorer-tab-${tab}`).onclick = () => selectTab(tab);
    $(`explorer-tab-${tab}`).onkeydown = event => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        selectTab(event.key === 'Home' ? 'files' : event.key === 'End' ? 'search' : tab === 'files' ? 'search' : 'files', true);
      }
    };
  }

  function renderFiles() {
    const needle = $('file-filter').value.trim().toLowerCase();
    const filtered = files.filter(file => file.path.toLowerCase().includes(needle));
    $('file-count').textContent = needle ? `${filtered.length}/${files.length}` : files.length;
    const root = { directories: new Map(), files: [] };
    for (const file of filtered) {
      let parent = root, path = '';
      const parts = file.path.split('/');
      for (const name of parts.slice(0, -1)) {
        path = path ? `${path}/${name}` : name;
        if (!parent.directories.has(name)) parent.directories.set(name, { directories: new Map(), files: [], path, count: 0 });
        parent = parent.directories.get(name); parent.count++;
      }
      parent.files.push({ ...file, name: parts.at(-1) });
    }
    function render(parent, depth = 0) {
      const fragment = document.createDocumentFragment();
      for (const [name, directory] of [...parent.directories].sort(([a], [b]) => a.localeCompare(b))) {
        const item = node('details', 'file-directory'); item.dataset.path = directory.path;
        item.open = !!needle || (expanded.get(directory.path) ?? depth === 0);
        const summary = node('summary'); summary.append(node('span', 'directory-name', name), node('small', '', directory.count));
        const children = node('div', 'directory-children'); children.append(render(directory, depth + 1));
        item.append(summary, children);
        item.addEventListener('toggle', () => { if (!needle && item.isConnected) expanded.set(directory.path, item.open); });
        fragment.append(item);
      }
      for (const file of parent.files.sort((a, b) => a.name.localeCompare(b.name))) {
        const button = node('button', 'file-item', file.name); button.type = 'button';
        button.title = `${file.path} · ${file.lines} 行`; button.setAttribute('aria-label', `打开文件 ${file.path}`);
        button.onclick = () => openFile(repo.id, file.path);
        fragment.append(button);
      }
      return fragment;
    }
    $('file-list').replaceChildren(filtered.length ? render(root) : node('p', 'empty-note', '没有匹配的文件。'));
  }
  $('file-filter').oninput = renderFiles;
  for (const [id, value] of [['tree-expand', true], ['tree-collapse', false]]) {
    $(id).onclick = () => {
      for (const directory of $('file-list').querySelectorAll('details')) {
        directory.open = value; expanded.set(directory.dataset.path, value);
      }
    };
  }

  function highlighted(text, query) {
    const fragment = document.createDocumentFragment();
    const lower = text.toLowerCase(), needle = query.toLowerCase();
    let offset = 0, index;
    while (needle && (index = lower.indexOf(needle, offset)) >= 0) {
      fragment.append(document.createTextNode(text.slice(offset, index)), node('mark', '', text.slice(index, index + query.length)));
      offset = index + query.length;
    }
    fragment.append(document.createTextNode(text.slice(offset)));
    return fragment;
  }

  function renderResults(data, repoId) {
    $('code-search-results').replaceChildren(...data.results.map(result => {
      const card = node('article', 'code-search-result');
      const open = node('button', 'code-result-open', `${result.path} · L${result.start}–${result.end}`); open.type = 'button';
      open.onclick = () => openFile(repoId, result.path, result.focusStart, result.focusEnd);
      const preview = node('pre', 'code-result-preview');
      for (const line of result.preview) {
        const row = node('span', 'preview-line');
        const number = node('span', 'preview-number', line.number); number.setAttribute('aria-hidden', 'true');
        const code = node('code'); code.append(highlighted(line.text, data.query)); row.append(number, code); preview.append(row);
      }
      card.append(open, preview);
      if (result.channels) card.append(node('small', 'retrieval-channels', result.channels.map(c => c === 'bm25' ? '关键词命中' : '语义召回').join(' + ')));
      return card;
    }));
    const scope = data.partial ? '当前为部分索引，仅搜索已收录文件。' : '仅搜索当前仓库的已索引文件。';
    $('code-search-status').textContent = data.total
      ? `「${data.query}」找到 ${data.total} 个片段，涉及 ${data.fileCount} 个文件。${data.truncated ? `显示前 ${data.results.length} 条，可添加路径筛选缩小范围。` : ''}${scope}`
      : `未找到「${data.query}」。可缩短关键词、调整路径，或切换到“相关片段”。${scope}`;
    if (data.retrieval) $('code-search-status').textContent += ` ${data.retrieval.actual === 'hybrid' ? 'BM25 + 向量，RRF 排序。' : '实际使用 BM25。'}${data.retrieval.warning || ''}候选池最多 ${data.candidateLimit} 个片段。`;
  }

  $('code-search-form').onsubmit = async event => {
    event.preventDefault();
    if (!repo) return;
    controller?.abort(); controller = null;
    const version = ++generation;
    const query = $('code-search-query').value.trim(), repoId = repo.id;
    $('code-search-results').replaceChildren();
    if (!query) { $('code-search-status').textContent = '请输入搜索内容。'; $('code-search-button').disabled = false; return; }
    controller = new AbortController();
    $('code-search-button').disabled = true; $('code-search-status').textContent = '正在搜索已索引文件…';
    const params = new URLSearchParams({ repo: repoId, q: query, mode: $('code-search-mode').value, path: $('code-search-path').value.trim() });
    try {
      const response = await fetch(`/api/search?${params}`, { signal: controller.signal });
      const data = await response.json();
      if (!response.ok) throw Error(data.error || '搜索失败，请重试。');
      if (version === generation) renderResults(data, repoId);
    } catch (error) {
      if (version === generation && error.name !== 'AbortError') $('code-search-status').textContent = error.message;
    } finally {
      if (version === generation) { controller = null; $('code-search-button').disabled = false; }
    }
  };

  function setRepository(next, indexedFiles) {
    const changed = repo?.id !== next.id || repo?.sha !== next.sha;
    repo = next; files = indexedFiles;
    if (changed) {
      generation++; controller?.abort(); controller = null; expanded.clear();
      for (const id of ['file-filter', 'code-search-query', 'code-search-path']) $(id).value = '';
      $('code-search-mode').value = 'literal'; $('code-search-button').disabled = false;
      $('code-search-results').replaceChildren(); $('code-search-status').textContent = '输入函数名或关键词搜索代码，无需调用模型。';
      selectTab('files');
    }
    renderFiles();
  }
  return { setRepository, renderFiles };
}
