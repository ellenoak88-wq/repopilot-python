const escapeMarkdown = value => String(value).replace(/[\\`*_[\]<>]/g, '\\$&').replace(/[\r\n]+/g, ' ');

export function checklistMarkdown(data) {
  const plan = data.plan;
  const lines = [`# ${escapeMarkdown(plan.repo)} · 项目上手清单`, '', `Commit: ${escapeMarkdown(plan.sha)}`, '',
    `手动完成：${plan.tasks.filter(t => t.completed).length}/${plan.tasks.length}。${data.stale ? '此清单对应旧索引，请重新核对依据。' : ''}`, '',
    '任务由已索引文件中的证据生成；勾选表示手动记录，不代表系统执行或验证了命令。', ''];
  for (const stage of data.stages) {
    lines.push(`## ${stage.title}`, '');
    for (const task of plan.tasks.filter(t => t.stage === stage.id)) {
      lines.push(`- [${task.completed ? 'x' : ' '}] ${escapeMarkdown(task.title)}`, '', task.description, '',
        `依据：${escapeMarkdown(task.source.path)} · L${task.source.start}–${task.source.end}`, '');
      const fence = '`'.repeat(Math.max(3, ...[...task.source.text.matchAll(/`+/g)].map(m => m[0].length + 1)));
      lines.push(fence + 'text', task.source.text, fence, '');
      if (task.source.truncated) lines.push('（以上为截取的证据片段，完整内容请打开源码。）', '');
      if (task.note) lines.push('备注：', ...task.note.split('\n').map(line => '> ' + escapeMarkdown(line)), '');
    }
    for (const gap of plan.gaps.filter(g => g.stage === stage.id)) lines.push(`待确认：${gap.message}`, '');
  }
  return lines.join('\n');
}

export function createChecklist(openFile) {
  const $ = id => document.getElementById(id);
  const make = (tag, cls, text) => {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  let repo = null, data = null, loaded = false, working = false, version = 0, reader = null, message = '';
  const drafts = new Map(), notesOpen = new Set();

  async function request(path, body, signal) {
    const response = await fetch(path, body === undefined ? { signal } : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
    });
    const value = await response.json();
    if (!response.ok) { const error = Error(value.error || '清单请求失败。'); error.status = response.status; throw error; }
    return value;
  }

  function actions() {
    $('checklist-generate').disabled = working || !loaded;
    $('checklist-refresh').disabled = working;
    $('checklist-export').disabled = working || !data?.plan || drafts.size > 0;
    $('checklist-generate').textContent = data?.plan ? '重新生成清单' : '生成上手清单';
    $('checklist-status').textContent = message || (working ? '正在加载…' : '');
  }

  function adopt(next) {
    if (data?.plan?.id !== next.plan?.id) { drafts.clear(); notesOpen.clear(); }
    data = next; loaded = true;
  }

  function render() {
    actions();
    const plan = data?.plan;
    const completed = plan?.tasks.filter(t => t.completed).length || 0;
    $('checklist-summary').textContent = plan ? `${completed}/${plan.tasks.length} 已完成${data.stale ? ' · 索引已变化' : ''}` : '生成路线，记录进度';
    $('checklist-progress').max = plan?.tasks.length || 1; $('checklist-progress').value = completed;
    $('checklist-progress-label').textContent = plan ? `${completed}/${plan.tasks.length} 项任务完成 · 手动记录` : '根据当前仓库证据生成，不调用模型';
    $('checklist-warning').classList.toggle('hidden', !data?.stale);
    $('checklist-warning').textContent = data?.stale ? '仓库索引已变化。旧清单仍保留，但进度记录和源码跳转已暂停；重新生成后会使用新索引并重置旧进度。' : '';
    $('checklist-meta').textContent = plan ? `${plan.fileCount} 个已索引文件 · ${plan.partial ? '部分索引 · ' : ''}commit ${plan.sha.slice(0, 10)} · 更新于 ${new Date(plan.updatedAt).toLocaleString('zh-CN')}` : '每项任务都有文件与行号依据；尚无依据的环节会标记为待确认。';
    const content = $('checklist-tasks'); content.replaceChildren();
    if (!plan) { content.append(make('p', 'checklist-empty', loaded ? '点击“生成上手清单”，整理阅读、环境、测试和贡献步骤。' : '正在读取清单…')); return; }
    for (const stage of data.stages) {
      const section = make('section', 'checklist-stage'); section.append(make('h3', '', stage.title));
      for (const task of plan.tasks.filter(t => t.stage === stage.id)) {
        const card = make('article', `checklist-task${task.completed ? ' is-complete' : ''}`); card.dataset.task = task.id;
        const label = make('label', 'checklist-task-title');
        const check = make('input'); check.type = 'checkbox'; check.checked = task.completed; check.id = `task-${task.id}`;
        check.disabled = working || data.stale;
        check.onchange = () => mutate('/api/checklist/update', { taskId: task.id, completed: check.checked }, check.id);
        label.append(check, make('span', '', task.title));
        const source = make('button', 'checklist-source', `${task.source.path} · L${task.source.start}–${task.source.end}`); source.type = 'button';
        source.disabled = data.stale; source.onclick = () => openFile(repo.id, task.source.path, task.source.start, task.source.end);
        const evidence = make('details', 'checklist-evidence'); evidence.append(make('summary', '', '查看依据原文'), make('pre', '', task.source.text));
        if (task.source.truncated) evidence.append(make('small', '', '此处为截取片段，可打开源码查看完整内容。'));
        const notes = make('details', 'checklist-notes'); notes.open = notesOpen.has(task.id);
        const summary = make('summary', '', drafts.has(task.id) ? '备注 · 未保存' : task.note ? '备注 · 已保存' : '添加备注');
        notes.addEventListener('toggle', () => { if (notes.isConnected) { if (notes.open) notesOpen.add(task.id); else notesOpen.delete(task.id); } });
        const text = make('textarea'); text.rows = 2; text.maxLength = 1000; text.value = drafts.get(task.id) ?? task.note;
        text.id = `note-${task.id}`; text.setAttribute('aria-label', `${task.title}的备注`); text.placeholder = '记录自己的理解或验证结果（最多 1000 字）'; text.disabled = working || data.stale;
        const save = make('button', 'secondary', '保存备注'); save.type = 'button'; save.id = `save-note-${task.id}`; save.disabled = working || data.stale || !drafts.has(task.id);
        text.oninput = () => {
          if (text.value === task.note) drafts.delete(task.id); else drafts.set(task.id, text.value);
          summary.textContent = drafts.has(task.id) ? '备注 · 未保存' : task.note ? '备注 · 已保存' : '添加备注';
          save.disabled = !drafts.has(task.id); actions();
        };
        save.onclick = () => mutate('/api/checklist/update', { taskId: task.id, note: text.value }, save.id);
        notes.append(summary, text, save, make('small', '', '保存后可跨会话保留，并包含在导出文件中。'));
        card.append(label, make('p', 'checklist-description', task.description), source, evidence, notes); section.append(card);
      }
      for (const gap of plan.gaps.filter(g => g.stage === stage.id)) section.append(make('p', 'checklist-gap', `待确认：${gap.message}`));
      content.append(section);
    }
  }

  async function load() {
    if (!repo) return;
    const token = ++version, repoId = repo.id;
    reader?.abort(); reader = new AbortController(); working = true; message = ''; render();
    try {
      const next = await request(`/api/checklist?repo=${encodeURIComponent(repoId)}`, undefined, reader.signal);
      if (token === version) adopt(next);
    } catch (error) {
      if (token === version && error.name !== 'AbortError') message = error.message;
    } finally { if (token === version) { working = false; render(); } }
  }

  async function mutate(path, extra = {}, focusId) {
    if (!repo || working || !loaded) return;
    const token = version, repoId = repo.id;
    const body = { repo: repoId, planId: data.plan?.id ?? null, revision: data.plan?.revision ?? 0, ...extra };
    working = true; message = ''; render();
    try {
      const next = await request(path, body);
      if (token !== version) return;
      if (Object.hasOwn(extra, 'note')) drafts.delete(extra.taskId);
      adopt(next);
      message = path.endsWith('/generate') ? '清单已生成。完成状态需手动记录。' : '进度与备注已保存。';
    } catch (error) {
      if (token !== version) return;
      message = error.message;
      if (error.status === 409) {
        try {
          const next = await request(`/api/checklist?repo=${encodeURIComponent(repoId)}`);
          if (token === version) adopt(next);
        } catch { /* Keep the error and offer an explicit refresh. */ }
      }
    } finally {
      if (token === version) { working = false; render(); if (focusId) $(focusId)?.focus(); }
    }
  }

  $('checklist-generate').onclick = () => {
    if (drafts.size) { message = '请先保存未保存的备注，再重新生成清单。'; actions(); return; }
    if (data?.stale && !confirm('索引已变化，重新生成将重置旧清单的完成状态和备注。继续？')) return;
    mutate('/api/checklist/generate');
  };
  $('checklist-refresh').onclick = () => { if (!working) load(); };
  $('checklist-export').onclick = () => {
    if (!data?.plan || drafts.size) return;
    const url = URL.createObjectURL(new Blob([checklistMarkdown(data)], { type: 'text/markdown;charset=utf-8' }));
    const link = make('a'); link.href = url; link.download = `${(repo.name || 'project').replace(/[^a-zA-Z0-9_.-]/g, '_')}-onboarding.md`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return {
    setRepository(next) {
      if (repo?.id === next.id && repo?.indexedAt === next.indexedAt) return;
      repo = next; data = null; loaded = false; drafts.clear(); notesOpen.clear();
      load();
    },
  };
}
