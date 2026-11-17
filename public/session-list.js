const make=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
export function createSessionList({root,onSelect,onAction}) {
  let busy=false,opened=null;
  function close(focus=false){if(!opened)return;opened.menu.hidden=true;opened.trigger.setAttribute('aria-expanded','false');if(focus)opened.trigger.focus();opened=null;}
  document.addEventListener('click',e=>{if(opened&&!opened.row.contains(e.target))close();});
  root.addEventListener('keydown',e=>{if(e.key==='Escape'){close(true);e.stopPropagation();}});
  return {
    setBusy(value){busy=value;if(value)close();for(const b of root.querySelectorAll('button'))b.disabled=value;},
    render(items,selectedId){
      close();root.replaceChildren(...items.map(session=>{
        const row=make('div',undefined,`session-row${session.id===selectedId?' selected':''}`);row.dataset.session=session.id;row.setAttribute('role','listitem');
        const choose=make('button',undefined,'session-select');choose.type='button';choose.setAttribute('aria-label',`切换会话：${session.title}`);
        if(session.id===selectedId)choose.setAttribute('aria-current','true');
        choose.append(make('span',`${session.pinned?'📌 ':''}${session.title}`,'session-row-title'),make('small',`${session.messageCount||0} 条回答${session.pinned?' · 已置顶':''}`));
        choose.onclick=()=>{close();if(!busy)onSelect(session.id);};
        const trigger=make('button','⋯','session-menu-trigger');trigger.type='button';trigger.setAttribute('aria-label',`会话菜单：${session.title}`);trigger.setAttribute('aria-haspopup','menu');trigger.setAttribute('aria-expanded','false');
        const menu=make('div',undefined,'session-menu');menu.id=`session-menu-${session.id}`;menu.hidden=true;menu.setAttribute('role','menu');menu.setAttribute('aria-label',`${session.title} 的操作`);trigger.setAttribute('aria-controls',menu.id);
        for(const [action,label] of [['pin',session.pinned?'取消置顶':'置顶会话'],['rename','重命名会话'],['delete','删除会话']]) {
          const button=make('button',label,action==='delete'?'danger':'');button.type='button';button.setAttribute('role','menuitem');button.onclick=()=>{close();if(!busy)onAction(action,session);};menu.append(button);
        }
        trigger.onclick=()=>{if(busy)return;const was=opened?.trigger===trigger;close();if(!was){menu.hidden=false;trigger.setAttribute('aria-expanded','true');opened={menu,trigger,row};menu.querySelector('button').focus();}};
        trigger.onkeydown=e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();trigger.click();}};
        menu.onkeydown=e=>{const buttons=[...menu.querySelectorAll('button')];let i=buttons.indexOf(document.activeElement);if(e.key==='ArrowDown')i=(i+1)%buttons.length;else if(e.key==='ArrowUp')i=(i+buttons.length-1)%buttons.length;else if(e.key==='Home')i=0;else if(e.key==='End')i=buttons.length-1;else return;e.preventDefault();buttons[i].focus();};
        row.append(choose,trigger,menu);return row;
      }));
      if(!items.length)root.append(make('p','没有匹配的会话。','empty-note'));
      for(const b of root.querySelectorAll('button'))b.disabled=busy;
    },
    focusMenu(id){root.querySelector(`[data-session="${CSS.escape(id)}"] .session-menu-trigger`)?.focus();}
  };
}
