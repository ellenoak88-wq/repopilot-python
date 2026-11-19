import { createSidebar } from './sidebar.js';
import { createSessionList } from './session-list.js';
import { createSections } from './sections.js';
import { createLearning } from './learning.js';
import { createWiki } from './wiki.js';
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
let chatController = null; let repoOpenVersion=0;
let modelCatalog = []; let modelConfigured = false; let active = null; let files = []; let history = []; let busy = false; let toastTimer;
const openFile = createReader(message => toast(message));
const explorer = createExplorer(openFile);
const checklist = createChecklist(openFile);
const retrieval = createRetrieval({setBusy, toast});
const discovery = createDiscovery(url => importRepository(url, true));
const learning = createLearning({api, toast});
const openWiki = createWiki({api, openFile});
$('wiki-open').onclick = () => { if (active && !busy) openWiki(active); };
const sectionsUI = createSections({api,toast,onChange:refreshRepos});
const sidebarUI = createSidebar({root:$('repo-list'),onOpen:id=>openRepo(id).catch(e=>toast(e.message)),onRepoAction:repoAction,onSectionAction:sectionAction,onMove:moveRepo});
const sessionUI = createSessionList({root:$('session-list'),onSelect:id=>openRepo(active.id,id).catch(e=>toast(e.message)),onAction:sessionAction});
const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json(); if (!response.ok) throw Error(data.error || '请求失败。'); return data;
}
function toast(message) { $('toast').textContent = message; $('toast').classList.remove('hidden'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').classList.add('hidden'), 9000); }
function setBusy(value) {
  busy = value;
  if (value) setModelMenu(false);
  discovery.setBusy(value);
  learning.setBusy(value);
  sectionsUI.setBusy(value);
  sidebarUI.setBusy(value);
  $('use-learning').disabled=value;
  retrieval.setBusy(value);
  for (const node of document.querySelectorAll('button, #repo-url, #model-choice, #custom-model, #effort-choice')) {
    if (node.closest('.code-reader, #sources, .repository-browser, #checklist-panel, #discover, #retrieval-panel, #learning, .learning-dialog, .sections-dialog, #repo-list, .sidebar-menu') || node.matches('.citation, .file-item')) continue;
    node.disabled = value;
  }
  $('send-button').classList.toggle('hidden', !!chatController);
  $('stop-button').disabled = !chatController;
  $('stop-button').classList.toggle('hidden', !chatController);
  sessionUI.setBusy(value); $('session-search').disabled=value;
  $('regenerate-button').disabled=value || !history.length || historical(history.at(-1));
  $('sync-button').disabled=value || !active || active.demo;
  if (!value) updateEfforts(false);
}
async function refreshRepos() {
  const [repos, sections] = await Promise.all([api('/api/repos'),api('/api/sections')]);
  if(active) {const updated=repos.find(r=>r.id===active.id);if(updated)active={...active,sectionId:updated.sectionId,pinned:updated.pinned};}
  discovery.setImported(repos); learning.setRepositories(repos); learning.setSections(sections); sectionsUI.setData(sections,repos,active);
  $('repo-count').textContent = repos.length;
  sidebarUI.render(sections,repos,active);
}
async function sidebarMutation(path,body,success,after) {
  if(busy)return;setBusy(true);
  try{await api(path,body);if(after)after();await refreshRepos();toast(success);}
  catch(e){toast(e.message);await refreshRepos().catch(()=>{});}
  finally{setBusy(false);}
}
function moveRepo(repo,sectionId){
  return sidebarMutation('/api/sections/assign',{repo:repo.id,sectionId,expectedSectionId:repo.sectionId},'项目已移动，分区共享范围已更新。');
}
function repoAction(action,repo){
  if(busy)return;
  if(action==='move'){sectionsUI.open(repo.id);return;}
  if(action==='pin'){sidebarMutation('/api/repos/pin',{repo:repo.id,pinned:!repo.pinned},repo.pinned?'已取消项目置顶。':'项目已在分区内置顶。');return;}
  if(action==='delete'&&confirm(`删除本地项目「${repo.id}」？将删除本地源码索引、该项目的全部会话、Wiki 专题、上手清单和向量索引。已保存的学习档案会保留并标为来源过期，GitHub 仓库不受影响。`)){
    sidebarMutation('/api/repos/delete',{repo:repo.id,indexVersion:repo.indexVersion},'本地项目已删除，学习档案已保留。',()=>{
      if(active?.id===repo.id){++repoOpenVersion;++sessionSearchVersion;active=null;currentSession=null;sessions=[];history=[];files=[];$('question').value='';showView('welcome');$('breadcrumb').textContent='仓库探索';}
    });
  }
}
function sectionAction(action,section){
  if(busy)return;
  if(action==='create'||action==='manage'){sectionsUI.open();return;}
  if(action==='rename'){const name=prompt('分区名称（最多 60 字）',section.name);if(name!==null)sidebarMutation('/api/sections/rename',{id:section.id,revision:section.revision,name},'分区已重命名。');}
  if(action==='delete'&&confirm(`删除分区「${section.name}」？仅可删除没有项目和共享档案的空分区。`))sidebarMutation('/api/sections/delete',{id:section.id,revision:section.revision},'空分区已删除。');
}
function historical(item) { return !!item?.indexVersion && item.indexVersion !== active?.indexVersion; }
function appendSnapshot(parent, source) {
  parent.append(el('pre', '', source.text));
  if (/^https:\/\/github\.com\//i.test(source.url || '')) {
    const link = el('a', '', '在 GitHub 查看原文');
    link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    parent.append(link);
  }
}
function canReadCurrent(item) {
  return !!item.indexVersion && item.indexVersion === active?.indexVersion;
}
function readSource(item, source) {
  return openFile(active.id, source.path, source.start, source.end, item.indexVersion);
}
function renderSources(chat) {
  const sources = chat?.sources || [];
  $('source-count').textContent = sources.length;
  if (!sources.length) {
    $('sources').replaceChildren(el('p', 'empty-note', '有源码依据的回答会在这里列出文件和引用片段。'));
    return;
  }
  $('sources').replaceChildren(...sources.map(source => {
    const details = el('details', 'source'); details.id = `source-${source.id}`;
    details.append(el('summary', '', `[${source.id}] ${source.path} · L${source.start}–${source.end}`));
    appendSnapshot(details, source);
    if (canReadCurrent(chat)) {
      const read = el('button', 'secondary', '阅读完整文件');
      read.type = 'button'; read.onclick = () => readSource(chat, source); details.append(read);
    } else details.append(el('p', 'muted', '此处保留回答生成时的源码摘录。'));
    return details;
  }));
}
function renderAnswer(container, item) {
  renderMarkdownAnswer(container, item.answer, item.sources, id => {
    renderSources(item);
    const target = $(`source-${id}`);
    if (target) target.open = true;
    if (canReadCurrent(item)) readSource(item, item.sources.find(source => source.id === id));
    else {
      const snapshot = container.parentElement.querySelector(`[data-snapshot="${id}"]`);
      if (!snapshot) return target?.scrollIntoView({block: 'nearest'});
      const more = snapshot.parentElement.closest('details');
      if (more) more.open = true;
      snapshot.open = true;
      snapshot.querySelector('summary').focus();
    }
  }, item.mode === 'agent');
}
function answerEvidence(item, content) {
  const byId = new Map(item.sources.map(source => [source.id, source]));
  const cited = new Set(Array.from(content.querySelectorAll('.citation'), button => button.textContent.slice(1, -1)));
  const ordered = new Set([...cited, ...byId.keys()]);
  const section = el('div', 'answer-evidence');
  const more = el('details', 'evidence-more');
  more.append(el('summary', '', `其他来源（${ordered.size - 1}）`));
  for (const id of ordered) {
    const source = byId.get(id);
    const current = canReadCurrent(item);
    const control = el(current ? 'button' : 'summary', 'evidence-row');
    const range = source.start === source.end ? source.start : `${source.start}–${source.end}`;
    control.append(el('span', 'evidence-action', current ? (cited.has(id) ? '查看依据' : '检索来源') : '查看摘录'),
      el('span', 'evidence-file', `[${id}] ${source.path}`), el('span', 'evidence-lines', `第 ${range} 行`));
    const parent = section.childElementCount ? more : section;
    if (current) {
      control.type = 'button'; control.onclick = () => readSource(item, source); parent.append(control);
    } else {
      const snapshot = el('details', 'evidence-snapshot'); snapshot.dataset.snapshot = id;
      snapshot.append(control); appendSnapshot(snapshot, source); parent.append(snapshot);
    }
  }
  if (ordered.size > 1) section.append(more);
  return section;
}
function addMessage(item) {
  const user = el('article', 'message user'); user.append(el('div', 'message-label', 'YOU'), el('div', 'message-content', item.question));
  const assistant = el('article', 'message assistant'); const label = el('div', 'message-label', 'REPOPILOT');
  label.append(el('small', '', item.mode === 'agent' ? `AGENT · ${item.model || '默认模型'} · ${item.effort || 'default'}` : '检索摘录 · 未调用生成模型'));
  if (historical(item)) label.append(el('small', '', `历史版本 · ${(item.repoSha || '未知').slice(0, 7)} · 当前源码已更新`));
  if (item.versionInferred) label.append(el('small', '', '版本由迁移时索引推定'));
  const content = el('div', 'message-content'); renderAnswer(content, item);
  const trace = el('details', 'trace'); trace.append(el('summary', '', `${item.trace.length} 个执行步骤 · 查看工具记录`));
  const steps = el('ol'); item.trace.forEach(s => steps.append(el('li', '', `${s.label} — ${s.detail}`))); trace.append(steps);
  assistant.append(label, content);
  if (item.sources.length) assistant.append(answerEvidence(item, content));
  assistant.append(trace);
  if (item.sessionSummary?.topics.length) {
    const { topics, olderTurns } = item.sessionSummary;
    const details = el('details', 'learning-memory session-summary');
    details.append(el('summary', '', `会话自动摘要 · ${topics.length} 个话题`));
    const description = item.mode === 'agent' ? '本次问答参考的历史摘录' : '当前会话的历史摘录';
    details.append(el('p', 'muted', `${description}，从较早的 ${olderTurns} 轮中选取。`));
    for (const topic of topics) {
      details.append(el('strong', '', topic.question), el('p', '', topic.answer));
    }
    assistant.append(details);
  }
  for (const memory of item.memoriesUsed || []) {
    const details=el('details','learning-memory');details.append(el('summary','',`[${memory.citation}] 学习经验 · ${memory.title} · 修订 ${memory.revision}`));
    details.append(el('p','muted',`${memory.repo || '通用经验'} · ${memory.scope==='global'?'跨仓库通用':memory.scope==='section'?`分区共享 · ${memory.sectionName||'分区'}`:'项目专属'} · 保存的是本次引用快照`),el('p','',`适用条件：${memory.applicability}`),el('pre','',memory.content));
    const edit=el('button','secondary','查看档案当前版本');edit.type='button';edit.onclick=()=>learning.open(memory.id);details.append(edit);assistant.append(details);
  }
  if(item.id) { const save=el('button','secondary save-learning','存为学习档案');save.type='button';save.onclick=()=>learning.saveChat(active.id,item.id);assistant.append(save); }
  $('messages').append(user, assistant);
  return { assistant, content, label, steps, trace };
}
async function openRepo(id, sessionId) {
  if (busy) return;
  const opening=++repoOpenVersion; ++sessionSearchVersion;
  const result = await api(`/api/repo?id=${encodeURIComponent(id)}${sessionId ? `&session=${encodeURIComponent(sessionId)}` : ''}`);
  if(opening!==repoOpenVersion)return;
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
  history.forEach(addMessage); renderSources(history.at(-1)); await refreshRepos();
  $('regenerate-button').disabled=!history.length || historical(history.at(-1));
  $('sync-button').disabled=!!active.demo;
  $('question').focus();
}
function renderSessions(items = sessions) {
  sessionUI.render(items,currentSession?.id);
  $('session-title').textContent=currentSession?.title || '会话';
  $('session-count').textContent=`${items.length} 个会话${$('session-search').value ? ' · 搜索结果':''}`;
}
async function refreshSessionList() {
  const version=++sessionSearchVersion, repo=active?.id;if(!repo)return;
  const items=await api(`/api/sessions?repo=${encodeURIComponent(repo)}&q=${encodeURIComponent($('session-search').value)}`);
  if(version===sessionSearchVersion && active?.id===repo){sessions=items;const selected=items.find(s=>s.id===currentSession?.id);if(selected)currentSession=selected;renderSessions(items);}
}
$('session-search').oninput=()=>refreshSessionList().catch(e=>toast(e.message));
$('new-session').onclick=async()=>{
  if(busy||!active)return;const repo=active.id;setBusy(true);
  try{const session=await api('/api/sessions/create',{repo,title:'新会话'});setBusy(false);await openRepo(repo,session.id);}catch(error){toast(error.message);}finally{setBusy(false);}
};
async function sessionAction(action,session) {
  if(busy||!active)return;
  const repo=active.id, body={repo,sessionId:session.id};
  if(action==='delete'&&!confirm(`删除「${session.title}」及其全部对话？此操作无法撤销，已保存的学习档案会保留。`))return;
  if(action==='rename'){const title=prompt('会话名称（最多 80 字）',session.title);if(title===null)return;body.title=title;}
  if(action==='pin')body.pinned=!session.pinned;
  setBusy(true); ++sessionSearchVersion;
  try{
    await api(`/api/sessions/${action}`,body);
    if(action==='delete'&&currentSession?.id===session.id){setBusy(false);await openRepo(repo);}
    else {await refreshSessionList();sessionUI.focusMenu(session.id);}
  }catch(error){toast(error.message);}finally{setBusy(false);sessionUI.focusMenu(session.id);}
}
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
  if (!modelConfigured) { toast('请先配置模型 API Key，再发送问题。'); return; }
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$/.test(selectedModel())) { toast('请选择模型或输入有效的模型 ID。'); return; }
  chatController = new AbortController();
  setBusy(true); $('send-button').textContent = '生成中…'; $('toast').classList.add('hidden');
  $('messages').querySelector('.empty-chat')?.remove();
  const item = { question, repoSha:active.sha, indexVersion:active.indexVersion, answer: '', sources: [], trace: [], mode: 'agent', model: selectedModel(), effort: $('effort-choice').value };
  const view = addMessage(item);
  view.content.textContent = '正在检索代码…';
  let completed = false; let frame;
  const paint = () => { frame = null; renderAnswer(view.content, item); $('messages').scrollTop = $('messages').scrollHeight; };
  try {
    const response = await fetch('/api/chat/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repo: active.id, sessionId:currentSession.id, regenerateId, question, model: item.model, effort: item.effort, retrieval: $('retrieval-mode').value, useLearning:$('use-learning').checked }), signal: chatController.signal });
    if (!response.ok) { const error = await response.json(); throw Error(error.error || '请求失败'); }
    for await (const message of readSSE(response.body)) {
      const event = JSON.parse(message.data);
      if (event.type === 'error') throw Error(event.error);
      if (event.type === 'answer-reset') item.answer = '';
      if (event.type === 'delta') { item.answer += event.delta; if (!frame) frame = requestAnimationFrame(paint); }
      if (event.type === 'source') { item.sources.push(event.source); renderSources(item); }
      if (event.type === 'step') {
        item.trace.push(event); view.steps.append(el('li', '', `${event.label} — ${event.detail}`));
        view.trace.querySelector('summary').textContent = `${event.label} · ${item.trace.length} 个执行步骤`;
        if (!item.answer) view.content.textContent = `${event.label}…`;
      }
      if (event.type === 'done') {
        completed = true; Object.assign(item, event.result);
        if(regenerateId!==undefined)history=history.map(h=>h.id===regenerateId?event.result:h);else history.push(event.result);
        cancelAnimationFrame(frame); paint(); renderSources(item); $('question').value = '';
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
    refreshSessionList().catch(e=>toast(e.message));
  }
}
$('stop-button').onclick = () => chatController?.abort();

$('chat-form').onsubmit = e => { e.preventDefault(); submitQuestion($('question').value); };
$('question').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitQuestion($('question').value); } };
document.querySelectorAll('[data-question]').forEach(button => {
  button.onclick = () => {
    const input = $('question');
    input.value = button.dataset.question;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  };
});
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
    const option = el('option', '', model.id === 'deepseek-flash' ? 'DeepSeek-V4.1-Flash' : model.label.split(' · ')[0]); option.value = model.id;
    options.push(option);
  }
  const custom = el('option', '', '自定义模型 ID…'); custom.value = '__custom__';
  $('model-choice').replaceChildren(...options, custom);
  $('model-choice').size = Math.min(8, options.length + 1);
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
  $('model-choice').disabled = busy;
  $('custom-model-label').classList.toggle('hidden', $('model-choice').value !== '__custom__');
  $('custom-model').disabled = busy;
  $('effort-choice').disabled = busy || !spec?.efforts.length;
  const name = model === 'deepseek-flash' ? 'DeepSeek-V4.1-Flash' : model || '自定义模型';
  $('model-list-toggle').textContent = $('model-choice').selectedOptions[0]?.textContent || name;
  const effort = $('effort-choice').value;
  $('model-name').textContent = name;
  $('effort-name').textContent = effort === 'default' ? '默认' : effort.charAt(0).toUpperCase() + effort.slice(1);
  $('model-toggle').setAttribute('aria-label', '选择模型与推理等级，' + name + '，' + $('effort-name').textContent);
  $('model-status').textContent = modelConfigured ? '● 当前模型 · ' + name : '请配置模型 API Key';
  try { if (model) localStorage.setItem('repopilot-model', model); localStorage.setItem('repopilot-effort', $('effort-choice').value); } catch {}
}
$('model-choice').onchange = () => updateEfforts();
$('model-list-toggle').onclick = () => setModelList($('model-choice').classList.contains('hidden'), true);
function setModelList(open, focus = false) {
  const list = $('model-choice');
  list.classList.toggle('hidden', !open);
  $('model-list-toggle').setAttribute('aria-expanded', String(open));
  if (focus) (open ? list : $('model-list-toggle')).focus({preventScroll: true});
  const selected = list.selectedOptions[0];
  if (open && selected) {
    const row = selected.getBoundingClientRect();
    const top = list.getBoundingClientRect().top + list.clientTop;
    const bottom = top + list.clientHeight;
    if (row.top < top) list.scrollTop += row.top - top;
    else if (row.bottom > bottom) list.scrollTop += Math.ceil(row.bottom - bottom);
  }
}
function finishModelChoice() {
  setModelList(false, true);
  if ($('model-choice').value === '__custom__') $('custom-model').focus();
}
$('model-choice').onclick = event => {
  if (event.target.tagName === 'OPTION') finishModelChoice();
};
$('model-choice').onkeydown = event => {
  if (event.key === 'Enter' || event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    if (event.key === 'Enter') finishModelChoice();
    else setModelList(false, true);
  }
};
$('custom-model').oninput = () => updateEfforts();
$('custom-model').onkeydown = event => {
  if (event.key === 'Enter' && !event.isComposing) event.preventDefault();
};
async function refreshModels(quiet = false) {
  if (busy) return;
  setBusy(true);
  try {
    const data = await api('/api/models');
    modelCatalog = [...data.models, ...modelCatalog.filter(m => !data.models.some(p => p.id === m.id))];
    renderModelChoices();

  } catch (error) { if (!quiet) toast(error.message); } finally { setBusy(false); }
}
$('refresh-models').onclick = () => refreshModels();
$('effort-choice').onchange = () => updateEfforts(false);
function setModelMenu(open, restoreFocus = false) {
  setModelList(false);
  $('model-panel').classList.toggle('hidden', !open);
  $('model-toggle').setAttribute('aria-expanded', String(open));
  if (open) $('model-list-toggle').focus();
  else if (restoreFocus) $('model-toggle').focus();
}
$('model-toggle').onclick = () => setModelMenu($('model-panel').classList.contains('hidden'));
document.addEventListener('pointerdown', event => {
  if (!event.target.closest('#model-panel, #model-toggle')) setModelMenu(false);
});
document.addEventListener('focusin', event => {
  if (!event.target.closest('#model-panel, #model-toggle')) setModelMenu(false);
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !$('model-panel').classList.contains('hidden')) {
    setModelMenu(false, true);
  }
});
async function init() {
  try {
    const status = await api('/api/status'); modelConfigured = status.modelConfigured;
    const limits = status.importLimits;
    if (limits) {
      const caption = `最多 ${limits.maxFiles.toLocaleString('zh-CN')} 个文本文件 · 总量 ${limits.maxTotalBytes / 1000000} MB`;
      $('import-limits').textContent = caption;
      $('discover-import-limits').textContent = `导入${caption}`;
    }
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
