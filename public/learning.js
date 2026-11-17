const $ = id => document.getElementById(id);
const node = (tag, text, className) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; };
const statuses = {draft:'待确认', verified:'已验证', archived:'已归档'};
const kinds = {experience:'实践经验', concept:'概念理解', procedure:'操作步骤'};

export function createLearning({api, toast}) {
  let records = [], repos = [], sections = [], editing = null, busy = false, pending = false, page = 0, total = 0, epoch = 0;
  const dialog = node('dialog', undefined, 'learning-dialog');
  dialog.innerHTML = `<form id="learning-editor">
    <div class="learning-heading"><h2 id="learning-editor-title">学习档案</h2><button type="button" id="learning-close" class="secondary">关闭</button></div>
    <p>将自己的理解整理成可复用的经验。已验证档案需要写明适用条件；项目事实仍以源码为准。</p>
    <label>标题<input id="learning-title" maxlength="120" required></label>
    <label>内容<textarea id="learning-content" rows="8" maxlength="6000" required></textarea></label>
    <label>适用条件与限制<textarea id="learning-applicability" rows="2" maxlength="1000" placeholder="例如：适用于 FastAPI 项目的本地开发；部署前需重新核对依赖版本。"></textarea></label>
    <div class="learning-fields"><label>类别<select id="learning-kind"><option value="experience">实践经验</option><option value="concept">概念理解</option><option value="procedure">操作步骤</option></select></label>
    <label>使用范围<select id="learning-scope"><option value="repo">项目专属</option><option value="section">分区共享</option><option value="global">跨仓库通用</option></select></label>
    <label id="learning-section-field">共享分区<select id="learning-section"></select></label>
    <label>来源仓库<select id="learning-repo"></select></label>
    <label>状态<select id="learning-status"><option value="draft">待确认</option><option value="verified">已验证</option><option value="archived">已归档</option></select></label></div>
    <label>标签（逗号分隔，最多 8 个）<input id="learning-tags" maxlength="247" placeholder="Python, 测试, 环境配置"></label>
    <p id="learning-version" class="muted"></p>
    <label id="learning-review-field" class="learning-check hidden"><input id="learning-review" type="checkbox">我已对照当前索引重新核对内容与适用条件</label>
    <details id="learning-origin"><summary>原始回答与来源快照</summary><pre id="learning-origin-text"></pre><div id="learning-origin-links"></div></details>
    <p id="learning-editor-error" role="alert"></p>
    <div class="learning-actions"><button type="submit" id="learning-save" class="primary">保存档案</button><button type="button" id="learning-delete" class="secondary">删除档案</button></div>
  </form>`;
  document.body.append(dialog);

  function controls() {
    for (const el of document.querySelectorAll('#learning button, #learning input, #learning select, .learning-dialog button, .learning-dialog input, .learning-dialog select, .learning-dialog textarea')) el.disabled = busy || pending;
    $('learning-repo').disabled = busy || pending || !!editing;
    $('learning-prev').disabled = busy || pending || page === 0;
    $('learning-next').disabled = busy || pending || (page+1)*20 >= total;
  }
  function repoOptions() {
    for (const [id, label] of [['learning-filter-repo','全部来源仓库'], ['learning-repo','无关联仓库']]) {
      const select = $(id), value = select.value;
      const empty = node('option',label); empty.value='';
      select.replaceChildren(empty,...repos.map(r => { const o=node('option',r.id); o.value=r.id; return o; }));
      if(id==='learning-repo' && editing?.repo && !repos.some(r=>r.id===editing.repo)){const missing=node('option',`${editing.repo}（已移除）`);missing.value=editing.repo;select.append(missing);}
      select.value = value;
    }
  }
  function sectionOptions() {
    for (const [id,label] of [['learning-section','请选择分区'],['learning-filter-section','全部分区']]) {
      const select=$(id),value=select.value,empty=node('option',label);empty.value='';
      select.replaceChildren(empty,...sections.map(s=>{const o=node('option',s.name);o.value=s.id;return o;}));select.value=value;
    }
    sectionField();
  }
  function sectionField(){ const shared=$('learning-scope').value==='section';$('learning-section-field').classList.toggle('hidden',!shared);$('learning-section').required=shared; }
  $('learning-scope').onchange=sectionField;
  async function refresh() {
    const version = ++epoch;
    $('learning-list-status').textContent = '正在读取学习档案…';
    const q = new URLSearchParams({q:$('learning-query').value,repo:$('learning-filter-repo').value,status:$('learning-filter-status').value,scope:$('learning-filter-scope').value,kind:$('learning-filter-kind').value,sectionId:$('learning-filter-section').value,offset:page*20,limit:20});
    try {
      const data = await api(`/api/learning?${q}`);
      if (version !== epoch) return;
      records=data.items;total=data.total;
      if (page && !records.length) { page=Math.max(0,Math.ceil(total/20)-1); return refresh(); }
      $('learning-list-status').textContent = `共 ${total} 条档案 · 第 ${page+1} 页`;
      $('learning-cards').replaceChildren(...records.map(card));
      if (!records.length) $('learning-cards').append(node('p','还没有匹配的档案。可新建一条，或在项目回答下选择“存为学习档案”。','empty-note'));
    } catch (e) { if (version === epoch) $('learning-list-status').textContent=e.message; }
    finally { controls(); }
  }
  function card(item) {
    const article=node('article',undefined,'learning-card');
    const heading=node('div',undefined,'learning-heading');
    const title=node('h2',item.title); const edit=node('button','编辑与核对','secondary');edit.type='button';edit.onclick=()=>open(item.id);
    heading.append(title,edit);
    article.append(heading,node('p',`${statuses[item.status]}${item.stale ? ' · 来源已更新，需复核' : ''} · ${item.scope==='global' ? '跨仓库通用':item.scope==='section'?`分区共享 · ${item.sectionName}`:'项目专属'} · ${kinds[item.kind]}`,'learning-meta'),
      node('p',item.repo || '个人通用经验','muted'),node('p',item.content.slice(0,500)+(item.content.length>500?'…':''),'learning-preview'));
    if(item.applicability)article.append(node('p',`适用条件：${item.applicability}`));
    if(item.tags.length)article.append(node('p',item.tags.map(t=>`#${t}`).join(' '),'learning-meta'));
    article.append(node('small',`修订 ${item.revision} · ${new Date(item.updatedAt).toLocaleString('zh-CN')}`,'muted'));
    return article;
  }
  function editor(item=null) {
    editing=item;repoOptions();sectionOptions();
    $('learning-editor-title').textContent=item ? '编辑学习档案':'新建学习档案';
    for(const key of ['title','content','applicability'])$(`learning-${key}`).value=item?.[key]||'';
    $('learning-kind').value=item?.kind||'experience';$('learning-scope').value=item?.scope||'global';
    $('learning-section').value=item?.sectionId||'';sectionField();
    $('learning-status').value=item?.status||'draft';$('learning-repo').value=item?.repo||'';
    $('learning-tags').value=item?.tags.join(', ')||'';$('learning-review').checked=false;
    $('learning-review-field').classList.toggle('hidden',!item?.stale);
    $('learning-version').textContent=item?.stale ? '来源索引已变化，此档案暂不参与问答。复核后可重新启用。' : (item ? `修订 ${item.revision} · 已验证且相关的档案才会参与问答。`:'跨仓库通用档案可在不同项目的对话中被检索。');
    $('learning-origin').classList.toggle('hidden',!item?.origin);$('learning-origin').open=false;
    $('learning-origin-links').replaceChildren();
    if(item?.origin) {
      const o=item.origin;
      $('learning-origin-text').textContent=`${o.repo} · commit ${o.repoSha || '未知'}${o.versionInferred ? '（迁移时推定）':''}\n\n${o.question}\n\n${o.answer}\n\n`+(o.sources||[]).map(s=>`[${s.id}] ${s.path}:${s.start}–${s.end}\n${s.text}`).join('\n\n')+'\n\n'+(o.memoriesUsed||[]).map(m=>`[${m.citation}] 历史经验 · ${m.title} · 修订 ${m.revision}\n适用条件：${m.applicability}\n${m.content}`).join('\n\n');
      for(const s of o.sources||[]) if(/^https:\/\/github\.com\//.test(s.url||'')) {
        const link=node('a',`[${s.id}] ${s.path} ↗`);link.href=s.url;link.target='_blank';link.rel='noopener noreferrer';$('learning-origin-links').append(link);
      }
    }
    $('learning-delete').classList.toggle('hidden',!item);$('learning-editor-error').textContent='';
    controls();if(!dialog.open)dialog.showModal();$('learning-title').focus();
  }
  async function open(id) {
    if(busy||pending)return;pending=true;controls();
    try { const item=await api(`/api/learning/detail?id=${encodeURIComponent(id)}`);pending=false;editor(item); }
    catch(e){toast(e.message);}finally{pending=false;controls();}
  }
  $('learning-editor').onsubmit=async event=>{
    event.preventDefault();if(busy||pending)return;pending=true;controls();
    const body={};for(const key of ['title','content','applicability','kind','scope','status','repo'])body[key]=$(`learning-${key}`).value;
    body.sectionId=$('learning-scope').value==='section'?$('learning-section').value:null;
    body.tags=$('learning-tags').value.split(/[,，]/).map(t=>t.trim()).filter(Boolean);
    if(editing)Object.assign(body,{id:editing.id,revision:editing.revision,reviewCurrent:$('learning-review').checked,currentIndexVersion:editing.currentIndexVersion});
    try { await api(editing?'/api/learning/update':'/api/learning/create',body);dialog.close();toast('学习档案已保存。');await refresh(); }
    catch(e){$('learning-editor-error').textContent=e.message;}
    finally{pending=false;controls();}
  };
  $('learning-delete').onclick=async()=>{
    if(busy||pending||!editing||!confirm(`删除学习档案「${editing.title}」？原始对话会保留。`))return;
    pending=true;controls();try{await api('/api/learning/delete',{id:editing.id,revision:editing.revision});dialog.close();await refresh();toast('学习档案已删除。');}
    catch(e){$('learning-editor-error').textContent=e.message;}finally{pending=false;controls();}
  };
  $('learning-close').onclick=()=>dialog.close();
  dialog.addEventListener('cancel',e=>{if(pending)e.preventDefault();});
  $('learning-new').onclick=()=>{if(!busy&&!pending)editor();};
  $('learning-filter').onsubmit=e=>{e.preventDefault();page=0;refresh();};
  for(const id of ['learning-filter-repo','learning-filter-status','learning-filter-scope','learning-filter-kind','learning-filter-section'])$(id).onchange=()=>{page=0;refresh();};
  $('learning-prev').onclick=()=>{page--;refresh();};$('learning-next').onclick=()=>{page++;refresh();};
  return {
    show:refresh,
    setBusy(value){busy=value;controls();},
    setRepositories(items){repos=items;repoOptions();},
    setSections(items){sections=items;sectionOptions();},
    open,
    async saveChat(repo,chatId){
      if(busy||pending)return;pending=true;controls();
      try{const item=await api('/api/learning/from-chat',{repo,chatId});pending=false;editor(item);toast('已保留原始回答快照，请整理内容与适用条件。');}
      catch(e){toast(e.message);}finally{pending=false;controls();}
    }
  };
}
