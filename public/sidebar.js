const make=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
export function createSidebar({root,onOpen,onRepoAction,onSectionAction,onMove}) {
  let busy=false,opened=null,dragging=null,serial=0;
  const expanded=new Map();
  const popup=make('div',undefined,'sidebar-menu');popup.hidden=true;popup.id='sidebar-context-menu';popup.setAttribute('role','menu');document.body.append(popup);
  function clearDrag(){dragging=null;root.querySelectorAll('.drop-target,.dragging').forEach(n=>n.classList.remove('drop-target','dragging'));}
  function close(focus=false){if(opened){opened.setAttribute('aria-expanded','false');if(focus&&opened.isConnected)opened.focus();}opened=null;popup.hidden=true;popup.replaceChildren();}
  function place(){
    if(!opened)return;const rect=opened.getBoundingClientRect();
    popup.style.left=`${Math.max(8,Math.min(rect.right-popup.offsetWidth,innerWidth-popup.offsetWidth-8))}px`;
    popup.style.top=`${Math.max(8,Math.min(rect.bottom+4,innerHeight-popup.offsetHeight-8))}px`;
  }
  function trigger(label,actions){
    const button=make('button','⋯','sidebar-menu-trigger');button.type='button';button.setAttribute('aria-label',label);button.title=label;
    button.setAttribute('aria-haspopup','menu');button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls',popup.id);
    button.onclick=e=>{
      e.stopPropagation();if(busy)return;const same=opened===button;close();if(same)return;
      opened=button;button.setAttribute('aria-expanded','true');popup.setAttribute('aria-label',label);
      for(const action of actions){const item=make('button',action.label,action.danger?'danger':'');item.type='button';item.setAttribute('role','menuitem');item.onclick=()=>{close();action.run();};popup.append(item);}
      popup.hidden=false;place();popup.querySelector('button')?.focus();
    };
    button.onkeydown=e=>{if(e.key==='ArrowDown'){e.preventDefault();if(!opened)button.click();}};
    return button;
  }
  popup.onkeydown=e=>{
    if(e.key==='Escape'){e.preventDefault();close(true);return;}
    const items=[...popup.querySelectorAll('button')];let i=items.indexOf(document.activeElement);
    if(e.key==='ArrowDown')i=(i+1)%items.length;else if(e.key==='ArrowUp')i=(i+items.length-1)%items.length;else if(e.key==='Home')i=0;else if(e.key==='End')i=items.length-1;else if(e.key==='Tab'){close();return;}else return;
    e.preventDefault();items[i].focus();
  };
  document.addEventListener('click',e=>{if(opened&&!popup.contains(e.target)&&!opened.contains(e.target))close();});
  window.addEventListener('resize',place);window.addEventListener('scroll',e=>{if(!(e.target instanceof Node)||!popup.contains(e.target))place();},true);
  document.addEventListener('dragend',clearDrag);
  return {
    setBusy(value){busy=value;if(value){close();clearDrag();}for(const b of root.querySelectorAll('button'))b.disabled=value;for(const b of root.querySelectorAll('.repo-item'))b.draggable=!value;},
    render(sections,repos,active){
      close();clearDrag();
      root.replaceChildren(...[...sections,{id:null,name:'未分区'}].map(section=>{
        const group=make('section',undefined,'repo-group');group.dataset.section=section.id||'';group.setAttribute('aria-label',`${section.name}分区`);
        const heading=make('div',undefined,'repo-group-heading'),items=make('div',undefined,'repo-group-items');items.id=`repo-group-items-${++serial}`;
        const members=repos.filter(r=>r.sectionId===section.id).sort((a,b)=>Number(b.pinned)-Number(a.pinned));
        const toggle=make('button',undefined,'repo-group-toggle');toggle.type='button';toggle.setAttribute('aria-controls',items.id);
        function setOpen(value){expanded.set(section.id,value);toggle.setAttribute('aria-expanded',String(value));toggle.textContent=`${value?'▾':'▸'} ${section.name} · ${members.length}`;items.hidden=!value;}
        setOpen(expanded.get(section.id)??true);toggle.onclick=()=>{if(!busy)setOpen(items.hidden);};
        const sectionActions=section.id?[
          {label:'重命名分区',run:()=>onSectionAction('rename',section)},
          {label:'删除空分区',danger:true,run:()=>onSectionAction('delete',section)},
          {label:'管理分区',run:()=>onSectionAction('manage',section)}
        ]:[{label:'新建分区',run:()=>onSectionAction('create',section)},{label:'管理分区',run:()=>onSectionAction('manage',section)}];
        heading.append(toggle,trigger(`分区菜单：${section.name}`,sectionActions));group.append(heading,items);
        for(const repo of members){
          const row=make('div',undefined,`repo-row${active?.id===repo.id?' selected':''}`);row.dataset.repo=repo.id;
          const button=make('button',`${repo.pinned?'📌':'⑂'}  ${repo.id}`,'repo-item');button.type='button';button.title=`${repo.id} · 可拖入分区`;button.setAttribute('aria-label',`打开项目：${repo.id}`);button.draggable=!busy;
          button.onclick=()=>{if(!busy&&!dragging)onOpen(repo.id);};
          button.ondragstart=e=>{if(busy){e.preventDefault();return;}close();dragging={...repo};row.classList.add('dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('application/x-repopilot-repo',repo.id);e.dataTransfer.setData('text/plain',repo.id);};
          button.ondragend=clearDrag;
          row.append(button,trigger(`项目菜单：${repo.id}`,[
            {label:repo.pinned?'取消置顶':'置顶项目',run:()=>onRepoAction('pin',repo)},
            {label:'移动到分区…',run:()=>onRepoAction('move',repo)},
            {label:'删除本地项目',danger:true,run:()=>onRepoAction('delete',repo)}
          ]));items.append(row);
        }
        if(!members.length)items.append(make('p','拖动项目到这里','repo-drop-hint'));
        group.ondragover=e=>{if(busy||!dragging)return;e.preventDefault();e.stopPropagation();e.dataTransfer.dropEffect=dragging.sectionId===section.id?'none':'move';
          root.querySelectorAll('.drop-target').forEach(n=>n.classList.remove('drop-target'));if(dragging.sectionId!==section.id)group.classList.add('drop-target');if(items.hidden)setOpen(true);};
        group.ondragleave=e=>{if(!group.contains(e.relatedTarget))group.classList.remove('drop-target');};
        group.ondrop=e=>{if(busy||!dragging)return;e.preventDefault();e.stopPropagation();const repo=dragging;clearDrag();if(repo.sectionId!==section.id)onMove(repo,section.id);};
        return group;
      }));
      for(const b of root.querySelectorAll('button'))b.disabled=busy;
    }
  };
}

