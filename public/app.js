import { createReader } from './reader.js';
import { readSSE } from './sse.js';
import { renderAnswer as renderMarkdownAnswer } from './render.js';
const $ = id => document.getElementById(id);
let currentSession = null; let sessions = []; let sessionSearchVersion = 0;
let chatController = null;
let modelCatalog = []; let modelConfigured = false; let active = null; let files = []; let history = []; let busy = false; let toastTimer;
const openFile = createReader(message => toast(message));
const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json(); if (!response.ok) throw Error(data.error || '请求失败。'); return data;
}
function toast(message) { $('toast').textContent = message; $('toast').classList.remove('hidden'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').classList.add('hidden'), 9000); }
function setBusy(value) {
  busy = value;
  for (const node of document.querySelectorAll('button, #repo-url, #mode, #model-choice, #custom-model, #effort-choice')) {
    if (node.closest('.code-reader, #sources') || node.matches('.citation, .file-item')) continue;
    node.disabled = value;
  }
  $('stop-button').disabled = !chatController;
  $('stop-button').classList.toggle('hidden', !chatController);
  $('session-choice').disabled=value; $('session-search').disabled=value;
  $('regenerate-button').disabled=value || !history.length;
  if (!value) updateEfforts(false);
}
async function refreshRepos() {
  const repos = await api('/api/repos'); $('repo-count').textContent = repos.length;
  $('repo-list').replaceChildren(...repos.map(repo => {
    const button = el('button', `repo-item${active?.id === repo.id ? ' selected' : ''}`, `⑂  ${repo.id}`);
    button.title = repo.id; button.disabled = busy; button.onclick = () => openRepo(repo.id).catch(e => toast(e.message)); return button;
  }));
}
function renderFiles() {
  const needle = $('file-filter').value.toLowerCase();
  $('file-list').replaceChildren(...files.filter(f => f.path.toLowerCase().includes(needle)).map(f => { const row = el('button', 'file-item'); row.type = 'button'; row.onclick = () => openFile(active.id, f.path); row.append(el('span', '', '⌑'), document.createTextNode(f.path)); row.title = `${f.lines} 行`; return row; }));
}
function renderSources(sources) {
  $('source-count').textContent = sources.length;
  $('sources').replaceChildren(...sources.map(source => {
    const item = el('details', 'source'); item.id = `source-${source.id}`;
    const read = el('button', 'secondary', '阅读完整文件'); read.type='button'; read.onclick=()=>openFile(active.id,source.path,source.start,source.end);
    item.append(el('summary', '', `[${source.id}] ${source.path} · L${source.start}–${source.end}`), el('pre', '', source.text));
    item.append(read);
    if (source.url) { const a = el('a', '', '在 GitHub 查看 ↗'); a.href = source.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; item.append(a); }
    return item;
  }));
}
function renderAnswer(container, answer, sources, markdown = true) {
  renderMarkdownAnswer(container, answer, sources, id => {
    renderSources(sources);
    const target = $(`source-${id}`);
    if (target) target.open = true;
    const source=sources.find(s=>s.id===id);if(source)openFile(active.id,source.path,source.start,source.end);
  }, markdown);
}
function addMessage(item) {
  const user = el('article', 'message user'); user.append(el('div', 'message-label', 'YOU'), el('div', 'message-content', item.question));
  const assistant = el('article', 'message assistant'); const label = el('div', 'message-label', 'REPOPILOT');
  label.append(el('small', '', item.mode === 'agent' ? `AGENT · ${item.model || '默认模型'} · ${item.effort || 'default'}` : '检索摘录 · 未调用模型'));
  const content = el('div', 'message-content'); renderAnswer(content, item.answer, item.sources, item.mode === 'agent');
  const trace = el('details', 'trace'); trace.append(el('summary', '', `${item.trace.length} 个执行步骤 · 查看工具记录`));
  const steps = el('ol'); item.trace.forEach(s => steps.append(el('li', '', `${s.label} — ${s.detail}`))); trace.append(steps);
  assistant.append(label, content, trace); $('messages').append(user, assistant);
  return { assistant, content, label, steps, trace };
}
async function openRepo(id, sessionId) {
  if (busy) return;
  const result = await api(`/api/repo?id=${encodeURIComponent(id)}${sessionId ? `&session=${encodeURIComponent(sessionId)}` : ''}`);
  active = result.repo; files = result.files; history = result.history; currentSession=result.session; sessions=result.sessions;
  $('session-search').value=''; renderSessions();
  $('welcome').classList.add('hidden'); $('project').classList.remove('hidden');
  $('breadcrumb').textContent = active.id; $('project-name').textContent = active.name; $('project-description').textContent = active.description;
  $('project-stats').replaceChildren(...[`${active.language}`, `${active.fileCount} 个文件`, `${active.chunkCount} 个片段`, active.demo ? '内置演示数据' : `commit ${active.sha.slice(0, 7)}`].map(t => el('span', '', t)));
  $('coverage').classList.toggle('hidden', !active.partial);
  $('coverage').textContent = `当前为部分索引：已收录 ${active.fileCount} 个文件（候选 ${active.totalEligible} 个，失败 ${active.failedFiles?.length || 0} 个）。未收录文件不会作为回答依据。`;
  $('file-count').textContent = active.fileCount; $('file-filter').value = ''; renderFiles();
  $('messages').replaceChildren();
  if (!history.length) { const empty = el('div', 'empty-chat'); empty.append(el('b', '', '从好奇，到理解。'), document.createTextNode('选择一个问题，或描述你想了解的代码。')); $('messages').append(empty); }
  history.forEach(addMessage); renderSources(history.at(-1)?.sources || []); await refreshRepos();
  $('regenerate-button').disabled=!history.length;
  $('question').focus();
}
function renderSessions(items = sessions) {
  $('session-choice').replaceChildren(...items.map(session=>{const option=el('option','',`${session.title} (${session.messageCount || 0})`);option.value=session.id;return option;}));
  if(items.some(s=>s.id===currentSession?.id))$('session-choice').value=currentSession.id;
  else $('session-choice').selectedIndex=-1;
  $('session-title').textContent=currentSession?.title || '会话';
  $('session-count').textContent=`${items.length} 个会话`;
}
$('session-choice').onchange=()=>openRepo(active.id,$('session-choice').value).catch(e=>toast(e.message));
$('session-search').oninput=async()=>{
  const version=++sessionSearchVersion; const repo=active.id;
  try{const items=await api(`/api/sessions?repo=${encodeURIComponent(repo)}&q=${encodeURIComponent($('session-search').value)}`);if(version===sessionSearchVersion&&active.id===repo)renderSessions(items);}catch(error){toast(error.message);}
};
$('new-session').onclick=async()=>{
  if(busy||!active)return;
  try{const session=await api('/api/sessions/create',{repo:active.id,title:'新会话'});await openRepo(active.id,session.id);}catch(error){toast(error.message);}
};
$('rename-session').onclick=async()=>{
  if(busy||!currentSession)return;const title=prompt('会话名称（最多 80 字）',currentSession.title);if(title===null)return;
  try{await api('/api/sessions/rename',{repo:active.id,sessionId:currentSession.id,title});await openRepo(active.id,currentSession.id);}catch(error){toast(error.message);}
};
$('delete-session').onclick=async()=>{
  if(busy||!currentSession||!confirm(`删除「${currentSession.title}」及其全部对话？此操作无法撤销。`))return;
  try{await api('/api/sessions/delete',{repo:active.id,sessionId:currentSession.id});await openRepo(active.id);}catch(error){toast(error.message);}
};
$('regenerate-button').onclick=()=>{const latest=history.at(-1);if(latest)submitQuestion(latest.question,latest.id);};
$('new-repo').onclick = () => { if (busy) return; $('project').classList.add('hidden'); $('welcome').classList.remove('hidden'); $('breadcrumb').textContent = '仓库探索'; $('repo-url').focus(); };
$('demo-button').onclick = async () => {
  if (busy) return; setBusy(true);
  try { const repo = await api('/api/demo', {}); setBusy(false); await openRepo(repo.id); } catch (e) { toast(e.message); } finally { setBusy(false); }
};
$('import-form').onsubmit = async event => {
  event.preventDefault(); if (busy) return; setBusy(true); $('progress-panel').classList.remove('hidden'); $('progress-text').textContent = '准备导入…'; $('progress-bar').value = 0;
  try {
    let job = await api('/api/import', { url: $('repo-url').value });
    while (job.state === 'running') {
      $('progress-text').textContent = job.message; $('progress-bar').value = job.progress;
      await new Promise(resolve => setTimeout(resolve, 700)); job = await api(`/api/jobs/${job.id}`);
    }
    if (job.state === 'error') throw Error(job.error);
    setBusy(false); await openRepo(job.repo.id);
  } catch (e) { toast(e.message); } finally { setBusy(false); $('progress-panel').classList.add('hidden'); }
};
async function submitQuestion(question, regenerateId) {
  if (!active || busy || !question.trim()) return;
  if ($('mode').value === 'agent' && !/^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$/.test(selectedModel())) { toast('请选择模型或输入有效的模型 ID。'); return; }
  chatController = new AbortController();
  setBusy(true); $('send-button').textContent = '生成中…'; $('toast').classList.add('hidden');
  $('messages').querySelector('.empty-chat')?.remove();
  const item = { question, answer: '', sources: [], trace: [], mode: $('mode').value, model: selectedModel(), effort: $('effort-choice').value };
  const view = addMessage(item);
  view.content.textContent = '正在检索代码…';
  let completed = false; let frame;
  const paint = () => { frame = null; renderAnswer(view.content, item.answer, item.sources, item.mode === 'agent'); $('messages').scrollTop = $('messages').scrollHeight; };
  try {
    const response = await fetch('/api/chat/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repo: active.id, sessionId:currentSession.id, regenerateId, question, mode: item.mode, model: item.model, effort: item.effort }), signal: chatController.signal });
    if (!response.ok) { const error = await response.json(); throw Error(error.error || '请求失败'); }
    for await (const message of readSSE(response.body)) {
      const event = JSON.parse(message.data);
      if (event.type === 'error') throw Error(event.error);
      if (event.type === 'answer-reset') item.answer = '';
      if (event.type === 'delta') { item.answer += event.delta; if (!frame) frame = requestAnimationFrame(paint); }
      if (event.type === 'source') { item.sources.push(event.source); renderSources(item.sources); }
      if (event.type === 'step') {
        item.trace.push(event); view.steps.append(el('li', '', `${event.label} — ${event.detail}`));
        view.trace.querySelector('summary').textContent = `${event.label} · ${item.trace.length} 个执行步骤`;
        if (!item.answer) view.content.textContent = `${event.label}…`;
      }
      if (event.type === 'done') {
        completed = true; Object.assign(item, event.result);
        if(regenerateId!==undefined)history=history.map(h=>h.id===regenerateId?event.result:h);else history.push(event.result);
        cancelAnimationFrame(frame); paint(); renderSources(item.sources); $('question').value = '';
        view.trace.querySelector('summary').textContent = `${item.trace.length} 个执行步骤 · 已完成`;
        if(regenerateId!==undefined){$('messages').replaceChildren();history.forEach(addMessage);}
      }
    }
    if (!completed) throw Error('连接提前结束，部分回答未保存。');
  } catch (error) {
    cancelAnimationFrame(frame);
    if (item.answer) paint();
    else view.content.textContent = chatController.signal.aborted ? '生成已停止。' : '未能完成回答。';
    view.label.append(el('small', '', chatController.signal.aborted ? '已停止 · 未保存' : '失败 · 未保存'));
    if (!chatController.signal.aborted) toast(error.message);
  } finally {
    chatController = null; setBusy(false); $('send-button').textContent = '发送 ↑';
  }
}
$('stop-button').onclick = () => chatController?.abort();

$('chat-form').onsubmit = e => { e.preventDefault(); submitQuestion($('question').value); };
$('question').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitQuestion($('question').value); } };
document.querySelectorAll('[data-question]').forEach(button => button.onclick = () => submitQuestion(button.dataset.question));
$('file-filter').oninput = renderFiles;
$('export-button').onclick = () => {
  if (!active) return;
  const text = `# ${active.id} · ${currentSession?.title || '探索笔记'}\n\nCommit: ${active.sha}\n\n` + history.map(h => `## ${h.question}\n\n${h.answer}\n\n### 参考来源\n${h.sources.map(s => `- [${s.id}] ${s.path}:${s.start}–${s.end}${s.url ? ` (${s.url})` : ''}`).join('\n')}`).join('\n\n---\n\n');
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' })); const a = el('a'); a.href = url; a.download = `${active.name}-${currentSession.id.slice(0,8)}-notes.md`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
function selectedModel() {
  return $('model-choice').value === '__custom__' ? $('custom-model').value.trim() : $('model-choice').value;
}
function renderModelChoices(selected = selectedModel()) {
  const groups = [el('optgroup'), el('optgroup')];
  groups[0].label = '接口返回的模型'; groups[1].label = '预设模型 · 需服务商支持';
  for (const model of modelCatalog) {
    const option = el('option', '', model.label); option.value = model.id;
    groups[model.discovered ? 0 : 1].append(option);
  }
  const custom = el('option', '', '自定义模型 ID…'); custom.value = '__custom__';
  $('model-choice').replaceChildren(...groups.filter(g => g.children.length), custom);
  if (modelCatalog.some(m => m.id === selected)) $('model-choice').value = selected;
  else { $('model-choice').value = '__custom__'; $('custom-model').value = selected; }
}
function updateEfforts(reset = true) {
  const previous = reset ? 'default' : $('effort-choice').value;
  const model = selectedModel();
  const spec = modelCatalog.find(m => m.id === model) || { efforts: /^gpt-(?:[6-9]|[1-9][0-9]|5\.)/.test(model) ? ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'] : [] };
  const labels = { default: '模型默认', none: '不启用推理', low: '低', medium: '中 · 推荐', high: '高', xhigh: '极高', max: '最高', ultra: '超高' };
  $('effort-choice').replaceChildren(...['default', ...(spec?.efforts || [])].map(v => { const o = el('option', '', labels[v]); o.value = v; return o; }));
  $('effort-choice').value = [...$('effort-choice').options].some(o => o.value === previous) ? previous : 'default';
  const extraction = $('mode').value === 'extractive';
  $('model-choice').disabled = busy || extraction;
  $('custom-model-label').classList.toggle('hidden', $('model-choice').value !== '__custom__');
  $('custom-model').disabled = busy || extraction;
  $('effort-choice').disabled = busy || extraction || !spec?.efforts.length;
  $('model-status').textContent = modelConfigured ? (extraction ? '● 检索模式 · 不调用模型' : `● 当前模型 · ${model || '请输入模型 ID'}`) : '● 检索模式 · 无需 API Key';
  try { if (model) localStorage.setItem('repopilot-model', model); localStorage.setItem('repopilot-effort', $('effort-choice').value); } catch {}
}
$('model-choice').onchange = () => updateEfforts();
$('custom-model').oninput = () => updateEfforts();
$('refresh-models').onclick = async () => {
  if (busy) return;
  setBusy(true);
  try {
    const data = await api('/api/models');
    modelCatalog = [...data.models, ...modelCatalog.filter(m => !data.models.some(p => p.id === m.id))];
    renderModelChoices();
    $('model-hint').textContent = `接口返回 ${data.models.length} 个模型；未返回的预设需由服务商确认。`;
  } catch (error) { toast(error.message); } finally { setBusy(false); }
};
$('effort-choice').onchange = () => updateEfforts(false);
$('mode').onchange = () => updateEfforts(false);
async function init() {
  try {
    const status = await api('/api/status'); modelConfigured = status.modelConfigured;
    if (!status.modelConfigured) { $('mode').value = 'extractive'; $('mode').querySelector('[value="agent"]').disabled = true; }
    modelCatalog = status.models;
    let savedModel, savedEffort;
    try { savedModel = localStorage.getItem('repopilot-model'); savedEffort = localStorage.getItem('repopilot-effort'); } catch {}
    renderModelChoices(savedModel && /^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$/.test(savedModel) ? savedModel : status.model);
    updateEfforts();
    if ([...$('effort-choice').options].some(o => o.value === savedEffort)) $('effort-choice').value = savedEffort;
    updateEfforts(false);
    await refreshRepos();
  } catch (e) { $('model-status').textContent = '连接失败'; toast(e.message); }
}
init();
