import { renderAnswer } from './render.js';

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(text, action, className = 'secondary') {
  const node = element('button', text, className);
  node.type = 'button';
  node.onclick = action;
  return node;
}

export function createWiki({api, openFile}) {
  const dialog = element('dialog', undefined, 'wiki-dialog');
  dialog.setAttribute('aria-labelledby', 'wiki-heading');
  dialog.innerHTML = `<div class="learning-heading"><h2 id="wiki-heading"></h2><button type="button" class="secondary">关闭</button></div>
    <p class="muted" data-repo></p><div data-body></div><p role="status"></p>`;
  document.body.append(dialog);
  const heading = dialog.querySelector('h2'), body = dialog.querySelector('[data-body]');
  const status = dialog.querySelector('[role=status]'), close = dialog.querySelector('button');
  let repo, pending = false;
  close.onclick = () => dialog.close();
  dialog.addEventListener('cancel', event => { if (pending) event.preventDefault(); });

  async function run(action) {
    if (pending) return;
    pending = true;
    body.inert = true;
    close.disabled = true;
    status.textContent = '正在读取…';
    try {
      await action();
      status.textContent = '';
    } catch (error) {
      status.textContent = error.message;
    } finally {
      pending = false;
      body.inert = false;
      close.disabled = false;
    }
  }

  function show(title, ...children) {
    heading.textContent = title;
    body.replaceChildren(...children);
    dialog.scrollTop = 0;
  }

  function detail(id) {
    return api('/api/wiki/detail?' + new URLSearchParams({repo:repo.id, id}));
  }

  function section(entry, version) {
    const article = element('article', undefined, 'wiki-entry');
    const current = !!entry.indexVersion && version === entry.indexVersion && !entry.versionInferred;
    const versionLabel = entry.versionInferred ? '推定版本' : current ? '当前索引' : '历史快照';
    article.append(element('h3', entry.question), element('small', versionLabel + ' · ' + (entry.repoSha || '未知').slice(0, 12), 'muted'));
    const content = element('div', undefined, 'message-content');
    const citations = new Map();
    const sources = entry.sources || [];
    renderAnswer(content, entry.content ?? entry.answer, sources, id => citations.get(id)?.(), entry.mode === 'agent');
    article.append(content);
    for (const source of sources) {
      const evidence = element('details', undefined, 'source');
      evidence.append(element('summary', '[' + source.id + '] ' + source.path + ' · L' + source.start + '–' + source.end),
                      element('pre', source.text));
      const read = () => openFile(repo.id, source.path, source.start, source.end, entry.indexVersion);
      citations.set(source.id, current ? read : () => { evidence.open = true; evidence.scrollIntoView({block:'nearest'}); });
      if (current) evidence.append(button('阅读完整文件', read));
      if (/^https:\/\/github\.com\//i.test(source.url || '')) {
        const link = element('a', '查看对应提交 ↗');
        link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
        evidence.append(link);
      }
      article.append(evidence);
    }
    for (const memory of entry.memoriesUsed || []) {
      const note = element('details', undefined, 'source');
      note.append(element('summary', '[' + memory.citation + '] ' + memory.title + ' · 学习经验快照'),
                  element('pre', [memory.applicability, memory.content].filter(Boolean).join('\n')));
      article.append(note);
    }
    return article;
  }

  let refreshTimer;
  dialog.addEventListener('close', () => clearTimeout(refreshTimer));

  function article(key, edition) {
    clearTimeout(refreshTimer);
    const page = edition.pages[key];
    const content = page.facts.map(f => f.text + ' ' + f.sources.map(id => '[' + id + ']').join(' ')).join('\n\n');
    const info = page.engine ? 'Agent 提炼 · ' + page.engine : '本地源码导览';
    const entry = section({question:info + ' · 修订 ' + page.revision, content, mode:page.engine ? 'agent' : 'extractive',
      sources:page.sources, indexVersion:edition.version, repoSha:page.sha}, edition.currentVersion);
    const related = element('div', undefined, 'wiki-list');
    if (page.related.length) related.append(element('h3', '关联专题'));
    for (const link of page.related) {
      const refs = link.sources.map(id => '[' + id + ']').join(' ');
      related.append(button(link.label + ' · ' + link.reason + ' ' + refs, () => article(link.key, edition)));
    }
    show(page.title, button('返回专题列表', () => run(library)), entry, related);
  }

  async function library() {
    clearTimeout(refreshTimer);
    const params = new URLSearchParams({repo:repo.id});
    const [edition, saved] = await Promise.all([api('/api/wiki/auto?' + params), api('/api/wiki?' + params)]);
    const list = element('div', undefined, 'wiki-list');
    if (edition.running) list.append(element('p', '正在根据项目源码更新专题…'));
    if (edition.error) list.append(element('p', edition.error));
    if (edition.version && edition.version !== edition.currentVersion) list.append(element('p', '以下专题来自上一次索引。'));
    for (const [key, page] of Object.entries(edition.pages)) {
      list.append(button(page.title + (page.engine ? '' : ' · 源码导览'), () => article(key, edition)));
    }
    if (!edition.running && !Object.keys(edition.pages).length) list.append(element('p', '当前索引没有可编写专题的文本。'));
    if (saved.length) {
      const archive = element('details');
      archive.append(element('summary', '手动保存的专题 · ' + saved.length));
      for (const page of saved) {
        archive.append(button(page.title, () => run(async () => {
          clearTimeout(refreshTimer);
          const item = await detail(page.id);
          const remove = button('删除手动专题', () => {
            if (!confirm('删除手动专题「' + item.title + '」及其中保存的全部结论？')) return;
            run(async () => {
              await api('/api/wiki/delete', {repo:repo.id, pageId:item.id, revision:item.revision});
              await library();
            });
          });
          const actions = element('div', undefined, 'learning-heading');
          actions.append(button('返回专题列表', () => run(library)), remove);
          show(item.title, actions,
               ...item.entries.map(entry => section(entry, item.currentIndexVersion)));
        })));
      }
      list.append(archive);
    }
    show('项目 Wiki', list);
    if (edition.running) refreshTimer = setTimeout(() => { if (dialog.open) run(library); }, 1800);
  }

  return async function open(repository) {
    if (pending || dialog.open) return;
    repo = repository;
    dialog.querySelector('[data-repo]').textContent = repo.id;
    show('项目 Wiki');
    dialog.showModal();
    await run(library);
  };
}

