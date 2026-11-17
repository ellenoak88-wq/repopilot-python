const $=id=>document.getElementById(id);
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
export function createSections({api,toast,onChange}) {
  let sections=[],repos=[],busy=false,pending=false,current=null;
  const dialog=el('dialog',undefined,'sections-dialog');
  dialog.innerHTML=`<div class="learning-heading"><h2>项目分区</h2><button id="sections-close" type="button" class="secondary">关闭</button></div>
    <p>将相关项目放入同一分区，共用范围为“分区共享”的已验证学习档案。</p>
    <form id="section-create-form"><label for="section-name">新分区名称</label><div class="section-create-row"><input id="section-name" maxlength="60" required placeholder="例如：Python 后端学习"><button class="primary" type="submit">创建分区</button></div></form>
    <p id="section-status" role="status"></p><div id="section-manager-list"></div>
    <h3>项目归属</h3><p class="muted">移动项目只改变可使用的分区档案；已保存的共享档案保留在原分区，可在档案编辑页修改范围。</p><div id="section-repos"></div>`;
  document.body.append(dialog);
  function options(select,value,empty='未分区') {
    const first=el('option',empty);first.value='';
    select.replaceChildren(first,...sections.map(s=>{const o=el('option',s.name);o.value=s.id;return o;}));select.value=value||'';
  }
  function controls(){
    for(const node of dialog.querySelectorAll('input,button,select'))node.disabled=busy||pending;
    $('project-section').disabled=busy||pending||!current;
    $('manage-sections').disabled=busy||pending;
  }
  async function mutate(action,body) {
    if(busy||pending)return;pending=true;controls();$('section-status').textContent='保存中…';
    try{await api(`/api/sections/${action}`,body);await onChange();$('section-status').textContent='已保存。';}
    catch(e){$('section-status').textContent=e.message;toast(e.message);await onChange().catch(()=>{});}
    finally{pending=false;controls();}
  }
  function render(){
    $('section-manager-list').replaceChildren(...sections.map(section=>{
      const row=el('div',undefined,'section-manager-row');row.append(el('strong',`${section.name} · ${section.repoCount} 个项目`));
      const rename=el('button','重命名','secondary');rename.type='button';rename.onclick=()=>{const name=prompt('分区名称（最多 60 字）',section.name);if(name!==null)mutate('rename',{id:section.id,revision:section.revision,name});};
      const remove=el('button','删除空分区','secondary');remove.type='button';remove.onclick=()=>{if(confirm(`删除分区「${section.name}」？仅可删除没有项目和共享档案的空分区。`))mutate('delete',{id:section.id,revision:section.revision});};
      row.append(rename,remove);return row;
    }));
    if(!sections.length)$('section-manager-list').append(el('p','尚未创建分区。','muted'));
    $('section-repos').replaceChildren(...repos.map(repo=>{
      const label=el('label',undefined,'section-manager-row');label.append(el('span',repo.id));
      const select=el('select');select.setAttribute('aria-label',`${repo.id} 所属分区`);options(select,repo.sectionId);
      select.onchange=()=>mutate('assign',{repo:repo.id,sectionId:select.value||null,expectedSectionId:repo.sectionId});label.append(select);return label;
    }));
    options($('project-section'),current?.sectionId);controls();
  }
  $('section-create-form').onsubmit=async e=>{e.preventDefault();await mutate('create',{name:$('section-name').value});};
  async function open(repoId){
    if(busy||pending)return;
    await onChange().catch(e=>toast(e.message));$('section-status').textContent='';if(!dialog.open)dialog.showModal();
    const select=[...$('section-repos').querySelectorAll('select')].find(s=>s.getAttribute('aria-label')===`${repoId} 所属分区`);
    if(select){select.scrollIntoView({block:'center'});select.focus();}else $('section-name').focus();
  }
  $('manage-sections').onclick=()=>open();
  $('sections-close').onclick=()=>dialog.close();dialog.addEventListener('cancel',e=>{if(pending)e.preventDefault();});
  $('project-section').onchange=()=>{if(current)mutate('assign',{repo:current.id,sectionId:$('project-section').value||null,expectedSectionId:current.sectionId});};
  return {open,setData(items,projects,active){sections=items;repos=projects;current=active;render();},setBusy(value){busy=value;controls();}};
}
