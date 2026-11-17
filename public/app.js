import { createLearning } from './learning.js';
import { createRetrieval } from './retrieval.js';
import { createReader } from './reader.js';
import { createExplorer } from './explorer.js';
import { createDiscovery } from './discovery.js';
import { createChecklist } from './checklist.js';
import { sortModels, isListedModel } from './model-order.js';
import { readSSE } from './sse.js';
import { renderAnswer as renderMarkdownAnswer } from './render.js';
const $ = id => document.getElementById(id);
let currentSession = null; let sessions = []; let sessionSearchVersion = 0;
let chatController = null;
let modelCatalog = []; let modelConfigured = false; let active = null; let files = []; let history = []; let busy = false; let toastTimer;
const openFile = createReader(message => toast(message));
const explorer = createExplorer(openFile);
const checklist = createChecklist(openFile);
const retrieval = createRetrieval({setBusy, toast});
const discovery = createDiscovery(url => importRepository(url, true));
const learning = createLearning({api, toast});
const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json(); if (!response.ok) throw Error(data.error || '请求失败。'); return data;
}
function toast(message) { $('toast').textContent = message; $('toast').classList.remove('hidden'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').classList.add('hidden'), 9000); }
function setBusy(value) {
  busy = value;
  discovery.setBusy(value);
  learning.setBusy(value);
  $('use-learning').disabled=value;
  retrieval.setBusy(value);
  for (const node of document.querySelectorAll('button, #repo-url, #mode, #model-choice, #custom-model, #effort-choice')) {
    if (node.closest('.code-reader, #sources, .repository-browser, #checklist-panel, #discover, #retrieval-panel, #learning, .learning-dialog') || node.matches('.citation, .file-item')) continue;
    node.disabled = value;
  }
  $('stop-button').disabled = !chatController;
  $('stop-button').classList.toggle('hidden', !chatController);
  $('session-choice').disabled=value; $('session-search').disabled=value;
  $('regenerate-button').disabled=value || !history.length || historical(history.at(-1));
  $('sync-button').disabled=value || !active || active.demo;
  if (!value) updateEfforts(false);
}
async function refreshRepos() {
  const repos = await api('/api/repos'); discovery.setImported(repos); learning.setRepositories(repos); $('repo-count').textContent = repos.length;
  $('repo-list').replaceChildren(...repos.map(repo => {
    const button = el('button', `repo-item${active?.id === repo.id ? ' selected' : ''}`, `⑂  ${repo.id}`);
    button.title = repo.id; button.disabled = busy; button.onclick = () => openRepo(repo.id).catch(e => toast(e.message)); return button;
  }));
}
function historical(item) { return !!item?.indexVersion && item.indexVersion !== active?.indexVersion; }
function renderSources(sources, old = false) {
  $('source-count').textContent = sources.length;
  $('sources').replaceChildren(...sources.map(source => {
    const item = el('details', 'source'); item.id = `source-${source.id}`;
    const read = el('button', 'secondary', '阅读完整文件'); read.type='button'; read.onclick=()=>openFile(active.id,source.path,source.start,source.end);
    item.append(el('summary', '', `[${source.id}] ${source.path} · L${source.start}–${source.end}`), el('pre', '', source.text));
    if (!old) item.append(read); else item.append(el('p', 'muted', '历史版本：此处保留当时的引用摘录，完整文件请查看对应 GitHub commit。'));
    if (source.url) { const a = el('a', '', '在 GitHub 查看 ↗'); a.href = source.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; item.append(a); }
    return item;
  }));
}
function renderAnswer(container, answer, sources, markdown = true, old = false) {
  renderMarkdownAnswer(container, answer, sources, id => {
    renderSources(sources, old);
    const target = $(`source-${id}`);
    if (target) target.open = true;
    const source=sources.find(s=>s.id===id);if(source && !old)openFile(active.id,source.path,source.start,source.end);else target?.scrollIntoView({block:"nearest"});
  }, markdown);
}
function addMessage(item) {
  const user = el('article', 'message user'); user.append(el('div', 'message-label', 'YOU'), el('div', 'message-content', item.question));
  const assistant = el('article', 'message assistant'); const label = el('div', 'message-label', 'REPOPILOT');
  label.append(el('small', '', item.mode === 'agent' ? `AGENT · ${item.model || '默认模型'} · ${item.effort || 'default'}` : '检索摘录 · 未调用生成模型'));
  if (historical(item)) label.append(el('small', '', `历史版本 · ${(item.repoSha || '未知').slice(0, 7)} · 当前源码已更新`));
  if (item.versionInferred) label.append(el('small', '', '版本由迁移时索引推定'));
  const content = el('div', 'message-content'); renderAnswer(content, item.answer, item.sources, item.mode === 'agent', historical(item));
  const trace = el('details', 'trace'); trace.append(el('summary', '', `${item.trace.length} 个执行步骤 · 查看工具记录`));
  const steps = el('ol'); item.trace.forEach(s => steps.append(el('li', '', `${s.label} — ${s.detail}`))); trace.append(steps);
  assistant.append(label, content, trace);
  for (const memory of item.memoriesUsed || []) {
    const details=el('details','learning-memory');details.append(el('summary','',`[${memory.citation}] 学习经验 · ${memory.title} · 修订 ${memory.revision}`));
    details.append(el('p','muted',`${memory.repo || '通用经验'} · ${memory.scope==='global'?'跨仓库通用':'项目专属'} · 保存的是本次引用快照`),el('p','',`适用条件：${memory.applicability}`),el('pre','',memory.content));
    const edit=el('button','secondary','查看档案当前版本');edit.type='button';edit.onclick=()=>learning.open(memory.id);details.append(edit);assistant.append(details);
  }
  if(item.id) { const save=el('button','secondary save-learning','存为学习档案');save.type='button';save.onclick=()=>learning.saveChat(active.id,item.id);assistant.append(save); }
  $('messages').append(user, assistant);
  return { assistant, content, label, steps, trace };
}
async function openRepo(id, sessionId) {
  if (busy) return;
  const result = await api(`/api/repo?id=${encodeURIComponent(id)}${sessionId ? `&session=${encodeURIComponent(sessionId)}` : ''}`);
  active = result.repo; files = result.files; history = result.history; currentSession=result.session; sessions=result.sessions;
  $('session-search').value=''; renderSessions();
  showView('project');
  $('breadcrumb').textContent = active.id; $('project-name').textContent = active.name; $('project-description').textContent = active.description;
  $('project-stats').replaceChildren(...[`${active.language}`, `${active.fileCount} 个文件`, `${active.chunkCount} 个片段`, active.demo ? '内置演示数据' : `commit ${active.sha.slice(0, 7)}`].map(t => el('span', '', t)));
  const sync = active.lastSync;
  $('sync-summary').textContent = sync?.previousSha ? `最近同步：新增 ${sync.added} · 更新 ${sync.updated} · 移出当前索引 ${sync.removed} · 未变 ${sync.unchanged}。历史会话与清单进度已保留。` : '';
  $('coverage').classList.toggle('hidden', !active.partial);
  const skipped = [active.skippedFileLimit ? `数量限制跳过 ${active.skippedFileLimit} 个` : '', active.skippedByteLimit ? `总量限制跳过 ${active.skippedByteLimit} 个` : '', active.treeTruncated ? 'GitHub 目录树返回不完整' : ''].filter(Boolean).join('；');
  $('coverage').textContent = `当前为部分索引：已收录 ${active.fileCount} 个文件（已发现候选 ${active.totalEligible} 个，失败 ${active.failedFiles?.length || 0} 个）。${skipped ? skipped + '。' : ''}未收录文件不会作为回答依据。`;
  explorer.setRepository(active, files);
  checklist.setRepository(active);
  retrieval.setRepository(active);
  $('messages').replaceChildren();
  if (!history.length) { const empty = el('div', 'empty-chat'); empty.append(el('b', '', '从好奇，到理解。'), document.createTextNode('选择一个问题，或描述你想了解的代码。')); $('messages').append(empty); }
  history.forEach(addMessage); renderSources(history.at(-1)?.sources || [], historical(history.at(-1))); await refreshRepos();
  $('regenerate-button').disabled=!history.length || historical(history.at(-1));
  $('sync-button').disabled=!!active.demo;
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
function showView(view) {
  ['welcome', 'project', 'discover', 'learning'].forEach(id => $(id).classList.toggle('hidden', id !== view));
  $('new-repo').classList.toggle('active', ['welcome','project'].includes(view));
  $('discover-nav').classList.toggle('active', view === 'discover');
  $('learning-nav').classList.toggle('active', view === 'learning');
}
$('new-repo').onclick = () => { if (busy) return; showView('welcome'); $('breadcrumb').textContent = '仓库探索'; $('repo-url').focus(); };
$('learning-nav').onclick=()=>{if(busy)return;showView('learning');$('breadcrumb').textContent='学习档案';learning.show();};
$('discover-nav').onclick = () => { if (busy) return; showView('discover'); $('breadcrumb').textContent = '发现项目'; discovery.show(); };
$('demo-button').onclick = async () => {
  if (busy) return; setBusy(true);
  try { const repo = await api('/api/demo', {}); setBusy(false); await openRepo(repo.id); } catch (e) { toast(e.message); } finally { setBusy(false); }
};
$('import-form').onsubmit = event => { event.preventDefault(); importRepository($('repo-url').value); };
$('sync-button').onclick = () => { if (active && !active.demo) importRepository(active.id, false, true); };
async function importRepository(url, reuseExisting = false, sync = false) {
  if (busy) return; setBusy(true); $('progress-panel').classList.remove('hidden'); $('progress-text').textContent = '准备导入…'; $('progress-bar').value = 0;
  try {
    const sessionId = sync ? currentSession?.id : undefined;
    let job = await api(sync ? '/api/repo/sync' : '/api/import', sync ? {repo: url} : { url, reuseExisting });
    while (job.state === 'running') {
      $('progress-text').textContent = job.message; $('progress-bar').value = job.progress;
      await new Promise(resolve => setTimeout(resolve, 700)); job = await api(`/api/jobs/${job.id}`);
    }
    if (job.state === 'error') throw Error(job.error);
    setBusy(false); await openRepo(job.repo.id, sessionId);
  } catch (e) { toast(e.message); } finally { setBusy(false); $('progress-panel').classList.add('hidden'); }
}
async function submitQuestion(question, regenerateId) {
  if (!active || busy || !question.trim()) return;
  if ($('mode').value === 'agent' && !/^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$/.test(selectedModel())) { toast('请选择模型或输入有效的模型 ID。'); return; }
  chatController = new AbortController();
  setBusy(true); $('send-button').textContent = '生成中…'; $('toast').classList.add('hidden');
  $('messages').querySelector('.empty-chat')?.remove();
  const item = { question, repoSha:active.sha, indexVersion:active.indexVersion, answer: '', sources: [], trace: [], mode: $('mode').value, model: selectedModel(), effort: $('effort-choice').value };
  const view = addMessage(item);
  view.content.textContent = '正在检索代码…';
  let completed = false; let frame;
  const paint = () => { frame = null; renderAnswer(view.content, item.answer, item.sources, item.mode === 'agent'); $('messages').scrollTop = $('messages').scrollHeight; };
  try {
    const response = await fetch('/api/chat/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repo: active.id, sessionId:currentSession.id, regenerateId, question, mode: item.mode, model: item.model, effort: item.effort, retrieval: $('retrieval-mode').value, useLearning:$('use-learning').checked }), signal: chatController.signal });
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
        $('messages').replaceChildren();history.forEach(addMessage);$('messages').scrollTop=$('messages').scrollHeight;
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
$('export-button').onclick = () => {
  if (!active) return;
  const text = `# ${active.id} · ${currentSession?.title || '探索笔记'}\n\nCommit: ${active.sha}\n\n` + history.map(h => `## ${h.question}\n\n回答版本：${h.repoSha || "未知"}${h.versionInferred ? "（迁移时推定）" : ""}\n\n${h.answer}\n\n### 参考来源\n${h.sources.map(s => `- [${s.id}] ${s.path}:${s.start}–${s.end}${s.url ? ` (${s.url})` : ''}`).join('\n')}`).join('\n\n---\n\n');
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' })); const a = el('a'); a.href = url; a.download = `${active.name}-${currentSession.id.slice(0,8)}-notes.md`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
function selectedModel() {
  return $('model-choice').value === '__custom__' ? $('custom-model').value.trim() : $('model-choice').value;
}
function renderModelChoices(selected = selectedModel()) {
  const models = sortModels(modelCatalog);
  if (!isListedModel(selected)) selected = models[0]?.id || '';
  const options = [];
  for (const model of models) {
    const option = el('option', '', model.label); option.value = model.id;
    options.push(option);
  }
  const custom = el('option', '', '自定义模型 ID…'); custom.value = '__custom__';
  $('model-choice').replaceChildren(...options, custom);
  if (models.some(m => m.id === selected)) $('model-choice').value = selected;
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
  $('model-status').textContent = modelConfigured ? (extraction ? '● 检索模式 · 不调用生成模型' : `● 当前模型 · ${model || '请输入模型 ID'}`) : '● 检索模式 · 无需 API Key';
  try { if (model) localStorage.setItem('repopilot-model', model); localStorage.setItem('repopilot-effort', $('effort-choice').value); } catch {}
}
$('model-choice').onchange = () => updateEfforts();
$('custom-model').oninput = () => updateEfforts();
async function refreshModels(quiet = false) {
  if (busy) return;
  setBusy(true);
  try {
    const data = await api('/api/models');
    modelCatalog = [...data.models, ...modelCatalog.filter(m => !data.models.some(p => p.id === m.id))];
    renderModelChoices();
    $('model-hint').textContent = `接口返回 ${data.models.length} 个模型。按时间从新到旧：优先版本日期，其余用接口创建时间；无时间预设置底并按版本倒序，需服务商支持。`;
  } catch (error) { if (!quiet) toast(error.message); } finally { setBusy(false); }
}
$('refresh-models').onclick = () => refreshModels();
$('effort-choice').onchange = () => updateEfforts(false);
$('mode').onchange = () => updateEfforts(false);
async function init() {
  try {
    const status = await api('/api/status'); modelConfigured = status.modelConfigured;
    const limits = status.importLimits;
    if (limits) {
      const caption = `最多 ${limits.maxFiles.toLocaleString('zh-CN')} 个文本文件 · 总量 ${limits.maxTotalBytes / 1000000} MB`;
      $('import-limits').textContent = caption;
      $('discover-import-limits').textContent = `导入${caption}`;
    }
    if (!status.modelConfigured) { $('mode').value = 'extractive'; $('mode').querySelector('[value="agent"]').disabled = true; }
    modelCatalog = status.models;
    let savedModel, savedEffort;
    try { savedModel = localStorage.getItem('repopilot-model'); savedEffort = localStorage.getItem('repopilot-effort'); } catch {}
    renderModelChoices(savedModel && /^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$/.test(savedModel) ? savedModel : status.model);
    updateEfforts();
    if ([...$('effort-choice').options].some(o => o.value === savedEffort)) $('effort-choice').value = savedEffort;
    updateEfforts(false);
    await refreshRepos();
    if (modelConfigured) await refreshModels(true);
  } catch (e) { $('model-status').textContent = '连接失败'; toast(e.message); }
}
init();
