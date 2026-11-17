"""User-curated, versioned learning records independent of chat lifetimes."""
import hashlib
import json
import uuid

from .retrieval import search
from .store import now
from .sections import SectionService


class LearningConflict(ValueError):
    pass


class LearningService:
    def __init__(self, store):
        self.store = store
        self.sections = SectionService(store)
        store.db.execute("""CREATE TABLE IF NOT EXISTS learning_records (
            id TEXT PRIMARY KEY, origin_key TEXT UNIQUE, payload TEXT NOT NULL)""")
        store.db.commit()

    def get(self, record_id):
        if not isinstance(record_id, str):
            raise ValueError("学习档案 ID 无效。")
        row = self.store.db.execute("SELECT payload FROM learning_records WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise ValueError("学习档案不存在，请刷新列表。")
        return self._view(json.loads(row[0]))

    def _view(self, item):
        repo = self.store.get(item['repo']) if item.get('repo') else None
        version = repo.get('indexVersion') if repo else None
        return {**item, 'sectionId':item.get('sectionId'), 'sectionName':self.sections.get(item['sectionId'])['name'] if item.get('sectionId') else None, 'currentIndexVersion': version,
                'stale': bool(item.get('repo') and (not repo or item.get('reviewedIndexVersion') != version))}

    def _all(self):
        return [self._view(json.loads(r[0])) for r in self.store.db.execute(
            "SELECT payload FROM learning_records ORDER BY json_extract(payload,'$.updatedAt') DESC,rowid DESC")]

    def list(self, *, query='', repo='', status='', scope='', kind='', section_id='', offset=0, limit=50):
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError('搜索词最多 200 字。')
        if status not in ('', 'draft', 'verified', 'archived') or scope not in ('', 'repo', 'section', 'global') or kind not in ('', 'experience', 'concept', 'procedure'):
            raise ValueError('学习档案筛选条件无效。')
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
            raise ValueError('分页参数无效。')
        records = [i for i in self._all() if (not repo or i['repo'] == repo)
                   and (not status or i['status'] == status) and (not scope or i['scope'] == scope)
                   and (not kind or i['kind'] == kind)
                   and (not section_id or i.get('sectionId') == section_id)
                   and query.lower() in ' '.join([i['title'], i['content'], i['applicability'], *i['tags']]).lower()]
        # List results omit bulky immutable evidence; the detail endpoint retains it.
        items = [{**i, 'origin': {k:v for k,v in i['origin'].items() if k not in ('sources', 'answer', 'memoriesUsed')}} if i.get('origin') else i
                 for i in records[offset:offset+limit]]
        return {'items': items, 'total': len(records), 'offset': offset, 'limit': limit}

    def _fields(self, body, previous=None):
        result = {}
        for key, maximum, required in [('title',120,True), ('content',6000,True), ('applicability',1000,False)]:
            value = body.get(key, previous.get(key, '') if previous else '')
            if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
                raise ValueError(f'{key} 需为{1 if required else 0}–{maximum}字文本。')
            result[key] = value.strip()
        for key, options, default in [('scope', ('repo','section','global'), 'repo'), ('kind', ('experience','concept','procedure'), 'experience'), ('status', ('draft','verified','archived'), 'draft')]:
            value = body.get(key, previous.get(key, default) if previous else default)
            if value not in options:
                raise ValueError(f'{key} 选项无效。')
            result[key] = value
        tags = body.get('tags', previous.get('tags', []) if previous else [])
        if not isinstance(tags, list) or len(tags) > 8 or any(not isinstance(t,str) or not 1 <= len(t.strip()) <= 30 for t in tags):
            raise ValueError('最多 8 个标签，每个标签 1–30 字。')
        result['tags'] = list(dict.fromkeys(t.strip() for t in tags))
        repo = body.get('repo', previous.get('repo') if previous else None) or None
        if repo is not None and (not isinstance(repo,str) or (not self.store.get(repo) and not (previous and repo == previous['repo']))):
            raise ValueError('关联仓库不存在。')
        if previous and repo != previous['repo']:
            raise ValueError('已有档案的来源仓库不可更改，请新建档案。')
        if result['scope'] == 'repo' and not repo:
            raise ValueError('项目专属档案必须选择仓库。')
        if result['status'] == 'verified' and not result['applicability']:
            raise ValueError('请填写适用条件后再标记为已验证。')
        section_id = body.get('sectionId', previous.get('sectionId') if previous else None)
        if result['scope'] == 'section':
            self.sections.get(section_id)
        else:
            section_id = None
        result['sectionId'] = section_id
        result['repo'] = repo
        return result

    def create(self, body, *, origin=None, origin_key=None):
        fields = self._fields(body)
        stamp = now()
        repo = self.store.get(fields['repo']) if fields['repo'] else None
        version = origin.get('indexVersion') if origin else (repo['indexVersion'] if repo else None)
        item = dict(id=str(uuid.uuid4()), revision=1, **fields, createdAt=stamp, updatedAt=stamp,
                    origin=origin, reviewedIndexVersion=version)
        with self.store.db:
            self.store.db.execute('INSERT INTO learning_records VALUES (?,?,?)', (item['id'], origin_key, json.dumps(item,ensure_ascii=False)))
        return self._view(item)

    def from_chat(self, repo, chat_id):
        if type(chat_id) is not int:
            raise ValueError('回答 ID 无效。')
        chat = self.store.get_chat(repo['id'], chat_id)
        if not chat:
            raise ValueError('回答不存在或不属于当前仓库。')
        key = hashlib.sha256(json.dumps([repo['id'],chat],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        existing = self.store.db.execute('SELECT id FROM learning_records WHERE origin_key=?',(key,)).fetchone()
        if existing:
            return self.get(existing[0])
        origin = {k:chat.get(k) for k in ('question','answer','sources','repoSha','indexVersion','versionInferred','sessionId','memoriesUsed')}
        origin.update(repo=repo['id'], chatId=chat_id)
        section_id = self.sections.membership(repo['id'])
        return self.create(dict(title=chat['question'][:120],content=chat['answer'][:6000],repo=repo['id'],scope='section' if section_id else 'repo',sectionId=section_id),
                           origin=origin, origin_key=key)

    def _expect(self, item, body):
        if type(body.get('revision')) is not int or body['revision'] != item['revision']:
            raise LearningConflict('档案已更新，请重新打开后再保存，避免覆盖其他页面的修改。')

    def update(self, body):
        with self.store.db:
            item = self.get(body.get('id'))
            self._expect(item,body)
            fields = self._fields(body,item)
            reviewed = item['reviewedIndexVersion']
            if 'reviewCurrent' in body and type(body['reviewCurrent']) is not bool:
                raise ValueError('复核参数必须为布尔值。')
            if body.get('reviewCurrent'):
                if not item['repo'] or not item['currentIndexVersion'] or body.get('currentIndexVersion') != item['currentIndexVersion']:
                    raise LearningConflict('仓库索引已变化，请重新打开档案并核对当前源码。')
                reviewed = item['currentIndexVersion']
            if fields['status'] == 'verified' and item['stale'] and (not item['currentIndexVersion'] or reviewed != item['currentIndexVersion']):
                raise LearningConflict('来源索引已变化，请核对当前源码并勾选复核后再标记为已验证，或保存为待确认。')
            updated = {k:v for k,v in item.items() if k not in ('stale','currentIndexVersion','sectionName')}
            updated.update(fields, revision=item['revision']+1,updatedAt=now(),reviewedIndexVersion=reviewed)
            self.store.db.execute('UPDATE learning_records SET payload=? WHERE id=?',(json.dumps(updated,ensure_ascii=False),item['id']))
        return self._view(updated)

    def delete(self, body):
        with self.store.db:
            item = self.get(body.get('id'))
            self._expect(item,body)
            self.store.db.execute('DELETE FROM learning_records WHERE id=?',(item['id'],))
        return {'deleted':item['id']}

    def eligible(self, repo):
        section_id = self.sections.membership(repo['id'])
        return [r for r in self._all() if r['status'] == 'verified' and not r['stale'] and (
            r['scope'] == 'global' or (r['scope'] == 'repo' and r['repo'] == repo['id'])
            or (r['scope'] == 'section' and section_id is not None and r.get('sectionId') == section_id))]

    def filter_history(self, repo, history, enabled):
        # A stale, edited or withdrawn note must not re-enter through an old answer.
        valid = {(r['id'], r['revision']) for r in self.eligible(repo)} if enabled else set()
        return [h for h in history if all((m['id'], m['revision']) in valid for m in h.get('memoriesUsed', []))]

    def recall(self, repo, question):
        records = self.eligible(repo)
        by_id = {r['id']:r for r in records}
        chunks = [dict(path=r['title'],text=r['content']+'\n'+r['applicability']+'\n'+' '.join(r['tags']),recordId=r['id']) for r in records]
        memories = []
        for hit in search(chunks,question,4):
            r = by_id[hit['recordId']]
            memories.append({k:r[k] for k in ('id','revision','title','scope','repo','kind','applicability','reviewedIndexVersion','sectionId','sectionName')}
                            | {'citation':f'M{len(memories)+1}', 'content':r['content'][:1500],
                               'originSha':(r.get('origin') or {}).get('repoSha')})
        return memories

    def export(self):
        sections = ['# 我的学习档案', '这是当前档案的 Markdown 快照；待确认、已过期和已归档内容不自动用于问答。']
        for item in self._all():
            sections.append(f"## {item['title']}\n\nID: {item['id']} · 修订 {item['revision']}\n\n"
                            f"状态：{item['status']}{' / 来源已过期' if item['stale'] else ''} · 范围：{item['scope']} · 分区：{item.get('sectionName') or '无'} · 仓库：{item['repo'] or '无'}\n\n"
                            f"标签：{', '.join(item['tags'])}\n\n适用条件：{item['applicability']}\n\n{item['content']}")
            origin = item.get('origin')
            if origin:
                sections.append(f"来源：{origin['repo']} · commit {origin.get('repoSha') or '未知'} · 回答 {origin['chatId']}\n\n"
                                + '\n'.join(f"- [{s['id']}] {s['path']}:{s['start']}–{s['end']} {s.get('url') or ''}" for s in origin.get('sources') or []))
        return '\n\n'.join(sections)+'\n'
