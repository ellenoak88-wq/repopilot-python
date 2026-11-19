"""Repository grouping, separate from source indexing and personal evidence."""
import sqlite3
import uuid

from .store import now


class SectionConflict(ValueError):
    pass


class SectionService:
    def __init__(self, store):
        self.store = store
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS project_sections (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL,
                revision INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS repo_sections (repo TEXT PRIMARY KEY, section_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS repo_preferences (repo TEXT PRIMARY KEY, pinned INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS repo_sections_group_idx ON repo_sections(section_id);
        ''')

    def get(self, section_id):
        if not isinstance(section_id, str):
            raise ValueError('分区 ID 无效。')
        item = self.store._one('SELECT id,name,revision,created_at FROM project_sections WHERE id=?', (section_id,))
        if not item:
            raise ValueError('分区不存在，请刷新。')
        return item

    def membership(self, repo):
        row = self.store._one('SELECT section_id FROM repo_sections WHERE repo=?', (repo,))
        return row['section_id'] if row else None

    def annotate(self, repo):
        preference = self.store._one('SELECT pinned FROM repo_preferences WHERE repo=?', (repo['id'],))
        return {**repo, 'sectionId': self.membership(repo['id']), 'pinned': bool(preference and preference['pinned'])}

    def repositories(self):
        return sorted((self.annotate(r) for r in self.store.list()), key=lambda r: not r['pinned'])

    def pin_repository(self, repo, pinned):
        if type(pinned) is not bool:
            raise ValueError('置顶状态必须为布尔值。')
        with self.store.db:
            self.store.db.execute('INSERT INTO repo_preferences VALUES (?,?) ON CONFLICT(repo) DO UPDATE SET pinned=excluded.pinned',
                                  (repo['id'],int(pinned)))
        return self.annotate(repo)

    def delete_repository(self, repo, expected_version):
        with self.store.db:
            current = self.store.get(repo['id'])
            if not current or expected_version != current['indexVersion']:
                raise SectionConflict('项目索引已变化，请刷新后重新确认删除。')
            # Personal learning records are independent; missing sources become stale.
            for table in ('files','chats','sessions','repo_sections','repo_preferences','checklists','vector_indexes','vector_batches',
                          'wiki_entries','wiki_pages','wiki_editions','wiki_focus'):
                if self.store._one("SELECT name FROM sqlite_master WHERE type='table' AND name=?",(table,)):
                    self.store.db.execute(f'DELETE FROM {table} WHERE repo=?',(repo['id'],))
            self.store.db.execute('DELETE FROM repositories WHERE id=?',(repo['id'],))
        return {'deleted':repo['id']}

    def list(self):
        return [dict(r) for r in self.store.db.execute('''SELECT s.id,s.name,s.revision,s.created_at,
            (SELECT COUNT(*) FROM repo_sections r WHERE r.section_id=s.id) AS repoCount
            FROM project_sections s ORDER BY s.created_at,s.rowid''')]

    def _name(self, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise ValueError('分区名称需为 1–60 字。')
        return name.strip()

    def create(self, name):
        name = self._name(name)
        key = str(uuid.uuid4())
        try:
            with self.store.db:
                self.store.db.execute('INSERT INTO project_sections VALUES (?,?,?,?,?)', (key,name,name.casefold(),1,now()))
        except sqlite3.IntegrityError as exc:
            raise SectionConflict('已有同名分区，请使用其他名称。') from exc
        return self.get(key)

    def _expect(self, body):
        item = self.get(body.get('id'))
        if type(body.get('revision')) is not int or body['revision'] != item['revision']:
            raise SectionConflict('分区已修改，请刷新后重试。')
        return item

    def rename(self, body):
        name = self._name(body.get('name'))
        try:
            with self.store.db:
                item = self._expect(body)
                self.store.db.execute('UPDATE project_sections SET name=?,name_key=?,revision=revision+1 WHERE id=?',
                                      (name,name.casefold(),item['id']))
        except sqlite3.IntegrityError as exc:
            raise SectionConflict('已有同名分区，请使用其他名称。') from exc
        return self.get(item['id'])

    def assign(self, body):
        repo, target = body.get('repo'), body.get('sectionId')
        if not isinstance(repo,str) or not self.store.get(repo):
            raise ValueError('仓库不存在。')
        if target is not None:
            self.get(target)
        with self.store.db:
            if 'expectedSectionId' not in body or body['expectedSectionId'] != self.membership(repo):
                raise SectionConflict('仓库所属分区已变化，请刷新后重试。')
            if target is None:
                self.store.db.execute('DELETE FROM repo_sections WHERE repo=?',(repo,))
            else:
                self.store.db.execute('INSERT INTO repo_sections VALUES (?,?) ON CONFLICT(repo) DO UPDATE SET section_id=excluded.section_id',(repo,target))
        return self.annotate(self.store.get(repo))

    def delete(self, body):
        with self.store.db:
            item = self._expect(body)
            if self.store._one('SELECT repo FROM repo_sections WHERE section_id=? LIMIT 1',(item['id'],)):
                raise SectionConflict('请先将分区内项目移出，再删除空分区。')
            exists = self.store._one("SELECT name FROM sqlite_master WHERE type='table' AND name='learning_records'")
            if exists and self.store._one("SELECT id FROM learning_records WHERE json_extract(payload,'$.scope')='section' AND json_extract(payload,'$.sectionId')=? LIMIT 1",(item['id'],)):
                raise SectionConflict('分区仍有共享档案，请先修改这些档案的共享范围，再删除分区。')
            self.store.db.execute('DELETE FROM project_sections WHERE id=?',(item['id'],))
        return {'deleted':item['id']}
