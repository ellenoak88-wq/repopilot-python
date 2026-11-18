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

function field(label, input) {
  const node = element('label', label);
  node.append(input);
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

  async function library() {
    const pages = await api('/api/wiki?' + new URLSearchParams({repo:repo.id}));
    const list = element('div', undefined, 'wiki-list');
    for (const page of pages) {
      list.append(button(page.title + ' · ' + page.revision + ' 条结论', () => run(() => readPage(page.id))));
    }
    if (!pages.length) list.append(element('p', '还没有专题页。在项目回答下选择「保存到 Wiki」即可创建。'));
    show('项目 Wiki', list);
  }

  async function readPage(id) {
    const page = await detail(id);
    show(page.title, button('返回专题列表', () => run(library)),
         ...page.entries.map(entry => section(entry, page.currentIndexVersion)));
  }

  function compose(draft, pages) {
    const select = element('select'), title = element('input'), content = element('textarea');
    select.append(new Option('新建专题页', ''), ...pages.map(page => new Option(page.title, page.id)));
    title.maxLength = 120;
    title.value = draft.question.slice(0, 120);
    const titleField = field('专题标题', title);
    select.onchange = () => { titleField.hidden = !!select.value; };
    content.rows = 8;
    content.value = draft.answer;
    const renderEditor = () => show('保存到 Wiki', field('目标专题', select), titleField,
      field('本次结论', content), button('预览变更', () => run(preview), 'primary'));

    async function preview() {
      const conclusion = content.value.trim();
      if (!conclusion || (!select.value && !title.value.trim())) throw Error('请填写专题标题和本次结论。');
      const page = select.value ? await detail(select.value) : null;
      if (page?.entries.some(entry => entry.originKey === draft.originKey)) throw Error('这条回答已经收录在此专题页。');
      const payload = {repo:repo.id, chatId:draft.chatId, originKey:draft.originKey};
      if (conclusion !== draft.answer.trim()) payload.content = conclusion;
      if (payload.content?.length > 12000) throw Error('编辑后的结论最多 12000 字。');
      const elements = [element('h3', page?.title || title.value.trim())];
      if (page) {
        Object.assign(payload, {pageId:page.id, revision:page.revision});
        const existing = element('details');
        existing.open = true;
        existing.append(element('summary', '已有内容 · ' + page.revision + ' 条结论'),
                        ...page.entries.map(entry => section(entry, page.currentIndexVersion)));
        elements.push(existing);
      } else payload.title = title.value.trim();
      const addition = element('div', undefined, 'wiki-addition');
      addition.append(element('strong', '本次新增'), section({...draft, content:conclusion}, page?.currentIndexVersion ?? repo.indexVersion));
      const actions = element('div', undefined, 'wiki-actions');
      const save = button(page ? '确认合并' : '创建专题页', () => run(async () => {
        const result = await api('/api/wiki/' + (page ? 'merge' : 'create'), payload);
        show('专题已保存', button('查看专题页', () => run(() => readPage(result.id))));
        await readPage(result.id);
      }), 'primary');
      actions.append(button('返回编辑', () => { status.textContent = ''; renderEditor(); }), save);
      show('预览变更', ...elements, addition, actions);
    }
    renderEditor();
  }

  return async function open(repository, chatId) {
    if (pending || dialog.open) return;
    repo = repository;
    dialog.querySelector('[data-repo]').textContent = repo.id;
    show('项目 Wiki');
    dialog.showModal();
    await run(async () => {
      if (!chatId) { await library(); return; }
      const [draft, pages] = await Promise.all([
        api('/api/wiki/draft', {repo:repo.id, chatId}),
        api('/api/wiki?' + new URLSearchParams({repo:repo.id}))
      ]);
      compose(draft, pages);
    });
  };
}

