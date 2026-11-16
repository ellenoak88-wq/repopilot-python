export function createReader(onError) {
  const dialog = document.createElement('dialog'); dialog.className = 'code-reader'; dialog.setAttribute('aria-label', '源码阅读器');
  // Static shell; repository content below is always assigned via textContent.
  dialog.innerHTML = `<div class="reader-heading"><div><strong id="reader-path"></strong><small id="reader-version"></small></div><button type="button" id="reader-close" class="secondary">关闭 ×</button></div>
    <div class="reader-tools"><label>跳转行 <input id="reader-line" type="number" min="1" value="1" aria-label="跳转行号"></label><button type="button" id="reader-jump" class="secondary">跳转</button><input id="reader-search" placeholder="在文件中查找…" aria-label="搜索源码"><button type="button" id="reader-next" class="secondary">下一个匹配</button><button type="button" id="reader-copy" class="secondary">复制源码</button></div>
    <div id="reader-info" role="status"></div><div class="reader-code" id="reader-code" tabindex="0"></div>`;
  document.body.append(dialog);
  const $ = id => dialog.querySelector(`#${id}`);
  let content = ''; let requestId = 0; let matches = []; let matchIndex = -1;
  const jump = line => { const row = $(`code-line-${line}`); if (row) { row.scrollIntoView({block:'center'}); $('reader-line').value = line; } };
  $('reader-close').onclick = () => dialog.close();
  $('reader-jump').onclick = () => jump(Math.max(1,Math.min(Number($('reader-line').max),Number($('reader-line').value)||1)));
  $('reader-search').oninput = () => {
    const query=$('reader-search').value.toLowerCase(); matches=[];matchIndex=-1;
    for(const row of $('reader-code').children) {
      const match=!!query && row.querySelector('code').textContent.toLowerCase().includes(query);
      row.classList.toggle('search-match',match); if(match)matches.push(row);
    }
    $('reader-info').textContent=query?`找到 ${matches.length} 个匹配行`:'引用行以绿色标记';
  };
  $('reader-next').onclick=()=>{if(matches.length){matchIndex=(matchIndex+1)%matches.length;matches[matchIndex].scrollIntoView({block:'center'});}};
  $('reader-copy').onclick=async()=>{try{await navigator.clipboard.writeText(content);$('reader-info').textContent='源码已复制';}catch{onError('无法访问剪贴板，请直接选择源码复制。');}};
  return async function openFile(repo, path, start=1, end=start) {
    const current=++requestId;
    $('reader-path').textContent=path; $('reader-version').textContent=''; $('reader-code').replaceChildren();
    $('reader-info').textContent='正在读取索引中的文件…';$('reader-search').value='';content='';matches=[];
    if(!dialog.open)dialog.showModal();
    try{
      const response=await fetch(`/api/file?repo=${encodeURIComponent(repo)}&path=${encodeURIComponent(path)}`);
      const data=await response.json();if(!response.ok)throw Error(data.error);
      if(current!==requestId)return;
      content=data.content; const lines=content.split('\n');
      start=Math.max(1,Math.min(lines.length,start));end=Math.max(start,Math.min(lines.length,end));
      $('reader-version').textContent=`${data.demo?'内置示例':`commit ${data.sha.slice(0,7)}`} · ${lines.length} 行`;
      $('reader-line').max=lines.length;
      const fragment=document.createDocumentFragment();
      lines.forEach((line,i)=>{const row=document.createElement('div');row.className='code-line';row.id=`code-line-${i+1}`;row.classList.toggle('cited-line',i+1>=start&&i+1<=end);
        const number=document.createElement('span');number.className='line-number';number.textContent=i+1;number.setAttribute('aria-hidden','true');
        const code=document.createElement('code');code.textContent=line||' ';row.append(number,code);fragment.append(row);});
      $('reader-code').append(fragment);$('reader-info').textContent=`已定位第 ${start}–${end} 行 · 可滚动查看前后文`;
      requestAnimationFrame(()=>jump(start));
    }catch(error){if(current===requestId)$('reader-info').textContent=error.message;}
  };
}
