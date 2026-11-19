export function createRetrieval({setBusy, toast}) {
  const $ = id => document.getElementById(id);
  let repo = null, busy = false, loading = false, generation = 0;
  let evaluation = null, evaluationTimer, starting = false;
  const split = $('evaluation-split');
  function sync() {
    $('retrieval-build').disabled = busy || loading || !repo;
    $('retrieval-refresh').disabled = busy || loading || !repo;
    $('retrieval-mode').disabled = busy;
    $('evaluation-run').disabled = busy || starting || !evaluation?.matched || evaluation?.job?.state === 'running';
    split.disabled = starting || evaluation?.job?.state === 'running';
  }
  async function request(path, body) {
    const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw Error(result.error || '向量索引请求失败。');
    return result;
  }
  function showEvaluation(value) {
    evaluation = value;
    clearTimeout(evaluationTimer);
    $('evaluation-panel').classList.toggle('hidden', !value);
    if (!value || !repo) { sync(); return; }
    const job = value.job;
    const link = $('evaluation-download');
    const params = new URLSearchParams({repo: repo.id, split: split.value});
    link.href = '/api/evaluation/report?' + params;
    link.classList.toggle('hidden', !value.reports.includes(split.value));
    const running = job?.state === 'running';
    const relevant = running || job?.split === split.value;
    $('evaluation-status').textContent = !value.matched ? '当前仓库版本与题集不匹配。'
      : relevant && job ? job.message : split.value === 'test' ? '保留集仅输出汇总指标。' : '同一份 JSON 报告对比 BM25 与混合检索。';
    sync();
    if (running) {
      const id = repo.id;
      evaluationTimer = setTimeout(async () => {
        try {
          const status = await request('/api/evaluation/status?repo=' + encodeURIComponent(id));
          if (repo?.id === id) showEvaluation(status);
        } catch (error) {
          if (repo?.id === id) {
            $('evaluation-status').textContent = error.message + '，请点击「检查索引状态」重试。';
          }
        }
      }, 2500);
    }
  }
  split.onchange = () => showEvaluation(evaluation);
  $('evaluation-run').onclick = async () => {
    if (!repo || !evaluation?.matched || starting) return;
    const id = repo.id;
    starting = true; sync();
    try {
      const job = await request('/api/evaluation/run', {repo:id, split:split.value});
      if (repo?.id === id) showEvaluation({...evaluation, job});
    } catch (error) {
      if (repo?.id === id) $('evaluation-status').textContent = error.message;
    } finally { starting = false; sync(); }
  };
  async function refresh() {
    if (!repo) return;
    const id = repo.id, revision = ++generation;
    loading = true; sync();
    try {
      const result = await request(`/api/retrieval/status?repo=${encodeURIComponent(id)}`);
      if (revision !== generation) return;
      showEvaluation(result.evaluation);
      const ready = result.state === 'ready';
      $('retrieval-state').textContent = ready ? '混合检索已就绪' : result.state === 'stale' ? '向量索引已过期 · 当前回退 BM25' : '尚未建立向量索引 · 当前使用 BM25';
      $('retrieval-build').textContent = result.state === 'missing' ? '建立向量索引' : '重建向量索引';
      $('retrieval-info').textContent = `${result.model} · ${ready ? `${result.indexedChunks} 个片段 / ${result.vectorCount} 个向量窗口` : `${result.chunkCount} 个待索引片段 · 窗口上限 ${result.windowLimit.toLocaleString('zh-CN')}`}${result.builtAt ? ` · 上次建立 ${new Date(result.builtAt).toLocaleString('zh-CN',{hour12:false})}` : ''}`;
    } catch (error) {
      if (revision === generation) $('retrieval-state').textContent = error.message;
    } finally { if (revision === generation) { loading = false; sync(); } }
  }
  $('retrieval-refresh').onclick = refresh;
  $('retrieval-build').onclick = async () => {
    if (!repo || busy || loading) return;
    setBusy(true);
    $('retrieval-progress').classList.remove('hidden'); $('retrieval-progress').value = 0;
    try {
      let job = await request('/api/retrieval/build', {repo:repo.id,force:true});
      while (job.state === 'running') {
        $('retrieval-state').textContent = job.message;
        $('retrieval-progress').value = job.progress;
        await new Promise(resolve => setTimeout(resolve,1000));
        job = await request(`/api/jobs/${job.id}`);
      }
      if (job.state === 'error') throw Error(job.error);
      await refresh();
    } catch (error) { $('retrieval-state').textContent = error.message; toast(error.message); }
    finally { $('retrieval-progress').classList.add('hidden'); setBusy(false); }
  };
  return {
    setRepository(value) { repo=value; split.value='dev'; showEvaluation(null); $('retrieval-state').textContent='正在检查向量索引…'; $('retrieval-info').textContent=''; refresh(); },
    setBusy(value) { busy=value; sync(); },
  };
}
