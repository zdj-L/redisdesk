"""Connection-bound runtime. I/O lives here, UI never owns credentials or Redis clients."""
from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from redisdesk.app.services.connection_service import open_gateway_with_secrets
from redisdesk.core.authz import Action, check_or_raise
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.redact import SensitiveMatcher


def display_bytes(value):
    if isinstance(value, bytes):
        try:
            text = value.decode('utf-8')
            if all(c.isprintable() or c in '\n\t' for c in text):
                return text
        except UnicodeDecodeError:
            pass
        return repr(value)
    if value is None:
        return '(nil)'
    return str(value)


class WorkspaceSession:
    """One immutable profile and one gateway per workspace; all calls serialized by the UI executor."""
    def __init__(self, service, session, profile):
        self.service, self.session, self.profile = service, session, profile
        self.gateway = None
        self.closed = False
        self.retired = threading.Event()
        self.lock = threading.RLock()
        self.matcher = SensitiveMatcher()
        self._db = None
        self._index_path = None
        self.cursor = 0
        self.scan_started = False
        self.scan_complete = False
        self.pattern = '*'
        self._scan_nodes = []
        self._scan_node_index = 0
        self.dbsize = None
        self.command_log = []
        self.on_command_log = None

    def connect(self):
        with self.lock:
            if self.closed or self.retired.is_set():
                raise AppError(ErrorCode.E01, '工作区已关闭，请重新打开。')
            check_or_raise(self.session, Action.CONNECT, self.profile, grants=self.service.grants)
            if self.gateway is not None:
                return self.profile
            if self.service.audit.is_chain_broken() or not self.service.audit._writable:
                raise AppError(ErrorCode.E13, '审计不可写，无法建立会话。')
            # Only this service boundary handles stored secrets; never return them to UI.
            if self.profile.secret_ref:
                ref = self.service.store.get_secret(self.profile.secret_ref)
                if ref is None:
                    raise AppError(ErrorCode.E02, '保存的凭据不存在，请编辑连接重新填写密码。')
            gw = None
            try:
                gw = open_gateway_with_secrets(self.service.store, self.profile)
                gw.ping()
                self.service._audit(self.session, self.profile, 'connect', 'allow', 'ok')
                if self.retired.is_set():
                    raise AppError(ErrorCode.E01, "工作区已关闭。")
                self.gateway = gw
                try:
                    if hasattr(gw, 'dbsize'):
                        self.dbsize = int(gw.dbsize())
                    else:
                        self.dbsize = int(gw.execute(['DBSIZE'], timeout_s=5))
                    self._record_log(['DBSIZE'], True, str(self.dbsize), 0, 'session')
                except Exception:
                    self.dbsize = None
                return self.profile
            except Exception:
                if gw is not None:
                    gw.close()
                raise

    def _ready(self, action):
        if self.closed or self.retired.is_set() or self.gateway is None:
            raise AppError(ErrorCode.E01, '尚未连接，请先连接此工作区。')
        check_or_raise(self.session, action, self.profile, grants=self.service.grants)
        return self.gateway

    def _record_log(self, argv, ok, summary, ms, source):
        entry = {
            'argv': [display_bytes(part) if not isinstance(part, str) else part for part in argv],
            'ok': bool(ok),
            'summary': '' if summary is None else str(summary)[:500],
            'ms': int(ms or 0),
            'source': source,
        }
        self.command_log = (self.command_log + [entry])[-300:]
        hook = self.on_command_log
        if hook is not None:
            try:
                hook(entry)
            except Exception:
                pass

    def _exec(self, gw, *args, timeout_s=5, source='browse'):
        started = time.perf_counter()
        argv = list(args)
        try:
            result = gw.execute(argv, timeout_s=timeout_s)
            ms = int((time.perf_counter() - started) * 1000)
            summary = display_bytes(result)
            if isinstance(result, (list, tuple, dict)):
                summary = f'{len(result)} 项'
            self._record_log(argv, True, summary, ms, source)
            return result
        except Exception as exc:
            ms = int((time.perf_counter() - started) * 1000)
            self._record_log(argv, False, getattr(exc, 'message', str(exc)), ms, source)
            raise

    def reset_scan(self, pattern='*'):
        self._ready(Action.BROWSE_KEYS)
        self._drop_index()
        fd, name = tempfile.mkstemp(prefix='redisdesk-key-index-', suffix='.sqlite')
        import os
        os.close(fd)
        self._index_path = Path(name)
        self._db = sqlite3.connect(name)
        self._db.execute('CREATE TABLE keys (id INTEGER PRIMARY KEY, raw BLOB UNIQUE NOT NULL)')
        self._db.execute(
            'CREATE TABLE prefixes (prefix BLOB PRIMARY KEY, parent BLOB, segment BLOB NOT NULL, n INTEGER NOT NULL)'
        )
        self._db.execute('CREATE INDEX prefixes_parent ON prefixes(parent)')
        self.cursor, self.scan_started, self.scan_complete = 0, False, False
        self.pattern = pattern or '*'
        self._scan_nodes = []
        self._scan_node_index = 0

    def _note_key(self, raw: bytes):
        parts = raw.split(b':', 12)
        if len(parts) == 1:
            return
        for i in range(1, len(parts)):
            prefix = b':'.join(parts[:i])
            parent = b':'.join(parts[:i - 1]) if i > 1 else None
            segment = parts[i - 1]
            self._db.execute(
                'INSERT INTO prefixes(prefix, parent, segment, n) VALUES (?, ?, ?, 1) '
                'ON CONFLICT(prefix) DO UPDATE SET n = n + 1',
                (sqlite3.Binary(prefix), sqlite3.Binary(parent) if parent is not None else None, sqlite3.Binary(segment)),
            )

    def _rebuild_prefixes(self):
        self._db.execute('DELETE FROM prefixes')
        for (raw,) in self._db.execute('SELECT raw FROM keys'):
            self._note_key(bytes(raw))
        self._db.commit()

    def scan_next(self):
        gw = self._ready(Action.BROWSE_KEYS)
        if self._db is None:
            self.reset_scan()
        if self.scan_complete:
            return self.key_page()
        started = time.perf_counter()
        # COUNT is a hint, not a server-side hard limit; one request per background task.
        node_id = None
        if getattr(gw, 'capabilities', None) is not None and gw.capabilities.supports_cluster:
            if not self._scan_nodes:
                nodes = [n for n in gw.nodes() if getattr(n, 'role', 'master') == 'master'] or list(gw.nodes())
                self._scan_nodes = [n.node_id for n in nodes]
                self._scan_node_index = 0
            if not self._scan_nodes:
                self.scan_complete = True
                return self.key_page()
            if self._scan_node_index >= len(self._scan_nodes):
                self.scan_complete = True
                return self.key_page()
            node_id = self._scan_nodes[self._scan_node_index]
        cursor, keys, _ = gw.scan(node_id, self.cursor, match=self.pattern, count=500, key_type=None)
        ms = int((time.perf_counter() - started) * 1000)
        added = 0
        for key in keys:
            self._db.execute('INSERT OR IGNORE INTO keys(raw) VALUES (?)', (sqlite3.Binary(key),))
            if self._db.execute('SELECT changes()').fetchone()[0]:
                self._note_key(key)
                added += 1
        self._db.commit()
        self.cursor, self.scan_started = cursor, True
        if node_id and cursor == 0:
            self._scan_node_index += 1
            self.cursor = 0
            self.scan_complete = self._scan_node_index >= len(self._scan_nodes)
        else:
            self.scan_complete = cursor == 0
        note = f'本批 {len(keys)}，新增 {added}'
        if node_id:
            note += f'（节点 {self._scan_node_index}/{len(self._scan_nodes)}）'
        self._record_log(['SCAN', str(self.cursor if not self.scan_complete else 0), 'MATCH', self.pattern, 'COUNT', '500'], True, note, ms, 'scan')
        return self.key_page()

    def key_page(self, page=0):
        self._ready(Action.BROWSE_KEYS)
        if self._db is None:
            return dict(keys=[], total=0, page=0, complete=False, cursor=0, namespace=self._empty_namespace())
        total = self._db.execute('SELECT COUNT(*) FROM keys').fetchone()[0]
        page = max(0, min(page, max(0, (total - 1) // 1000)))
        rows = self._db.execute('SELECT raw FROM keys ORDER BY id LIMIT 1000 OFFSET ?', (page * 1000,)).fetchall()
        payload = dict(
            keys=[bytes(row[0]) for row in rows],
            total=total,
            page=page,
            complete=self.scan_complete,
            cursor=self.cursor,
            namespace=self.tree_children(None, 0, 400),
            dbsize=self.dbsize,
            tree_mode='lazy',
        )
        if total <= 2000:
            payload['tree'] = self._full_tree()
            payload['tree_mode'] = 'full'
        return payload

    def _full_tree(self, prefix=None):
        node = self.tree_children(prefix, 0, 2000)
        node['children'] = [dict(folder, child=self._full_tree(folder['prefix'])) for folder in node['folders']]
        return node

    def _empty_namespace(self, prefix=None):
        return dict(prefix=prefix, folders=[], leaves=[], leaf_total=0, more=False, offset=0)

    def tree_children(self, prefix=None, offset=0, limit=400):
        if self._db is None:
            return self._empty_namespace(prefix)
        key_total = self._db.execute('SELECT COUNT(*) FROM keys').fetchone()[0]
        prefix_total = self._db.execute('SELECT COUNT(*) FROM prefixes').fetchone()[0]
        if key_total and not prefix_total:
            self._rebuild_prefixes()
        if prefix is None:
            folder_rows = self._db.execute(
                'SELECT prefix, segment, n FROM prefixes WHERE parent IS NULL ORDER BY segment'
            ).fetchall()
            leaf_rows = [bytes(row[0]) for row in self._db.execute('SELECT raw FROM keys ORDER BY id') if b':' not in bytes(row[0])]
        else:
            folder_rows = self._db.execute(
                'SELECT prefix, segment, n FROM prefixes WHERE parent = ? ORDER BY segment',
                (sqlite3.Binary(prefix),),
            ).fetchall()
            needle = prefix + b':'
            depth = prefix.count(b':') + 1
            leaf_rows = []
            for (raw,) in self._db.execute('SELECT raw FROM keys ORDER BY id'):
                data = bytes(raw)
                if not data.startswith(needle):
                    continue
                rest = data[len(needle):]
                if b':' not in rest or depth >= 12:
                    leaf_rows.append(data)
        folders = [{'prefix': bytes(row[0]), 'segment': bytes(row[1]), 'count': int(row[2])} for row in folder_rows]
        total = len(leaf_rows)
        offset = max(0, offset)
        leaves = leaf_rows[offset:offset + limit]
        return dict(prefix=prefix, folders=folders, leaves=leaves, leaf_total=total, more=offset + len(leaves) < total, offset=offset)

    def read_value(self, key: bytes, token=0, expected_type=None):
        gw = self._ready(Action.VIEW_VALUE)
        command = lambda *args: self._exec(gw, *args, source='browse')
        kind = display_bytes(command('TYPE', key))
        ttl = int(command('TTL', key))
        if expected_type and kind != expected_type:
            raise AppError(ErrorCode.E04, '键类型已变化，请重新点击键刷新。')
        rows, more, next_token, total = [], False, None, None
        identities = []
        def masked(value, field=None):
            text = display_bytes(value)
            return self.matcher.match(key=key.decode('utf-8', errors='surrogateescape'), field=field,
                                      value=text, value_len=len(value) if isinstance(value, bytes) else len(text)).text
        if kind == 'none':
            rows = [('键已过期或被删除',)]
        elif kind == 'string':
            total = int(command('STRLEN', key))
            if token == 0 and total <= 65536:
                data = command('GET', key)
            else:
                data = command('GETRANGE', key, str(token), str(token + 65535))
            rows = [(masked(data),)]
            next_token = token + (len(data) if isinstance(data, bytes) else 0)
            more = next_token < total
        elif kind in {'hash', 'set', 'zset'}:
            scan = {'hash': 'HSCAN', 'set': 'SSCAN', 'zset': 'ZSCAN'}[kind]
            size = {'hash': 'HLEN', 'set': 'SCARD', 'zset': 'ZCARD'}[kind]
            total = int(command(size, key))
            next_token, payload = command(scan, key, str(token), 'COUNT', '100')
            next_token = int(next_token)
            more = next_token != 0
            if kind == 'hash':
                pairs = payload.items() if isinstance(payload, dict) else zip(payload[::2], payload[1::2])
                pairs = list(pairs)
                rows = [(display_bytes(k), masked(v, display_bytes(k))) for k, v in pairs]
                identities = [display_bytes(k) for k, v in pairs]
            elif kind == 'zset':
                pairs = payload if not payload or isinstance(payload[0], (list, tuple)) else zip(payload[::2], payload[1::2])
                pairs = list(pairs)
                rows = [(masked(k), display_bytes(score)) for k, score in pairs]
                identities = [display_bytes(k) for k, score in pairs]
            else:
                members = list(payload)
                rows = [(masked(k),) for k in members]
                identities = [display_bytes(k) for k in members]
        elif kind == 'list':
            total = int(command('LLEN', key))
            values = command('LRANGE', key, str(token), str(token + 99))
            rows = [(str(token + i), masked(v)) for i, v in enumerate(values)]
            identities = [str(token + i) for i in range(len(values))]
            next_token = token + len(values)
            more = next_token < total
        elif kind == 'stream':
            total = int(command('XLEN', key))
            entries = command('XRANGE', key, token if token else '-', '+', 'COUNT', '100')
            for entry_id, fields in entries:
                pairs = fields.items() if isinstance(fields, dict) else zip(fields[::2], fields[1::2])
                rows.append((display_bytes(entry_id), '\n'.join(display_bytes(f) + ': ' + masked(v, display_bytes(f)) for f, v in pairs)))
                identities.append(display_bytes(entry_id))
            if entries:
                major, minor = display_bytes(entries[-1][0]).split('-')
                next_token = major + '-' + str(int(minor) + 1)
            more = len(entries) == 100
        else:
            rows = [('暂不支持此类型：' + kind,)]
        return dict(key=key, kind=kind, ttl=ttl, total=total, rows=rows, more=more, token=next_token, identities=identities)

    def prepare_console(self, console, raw, *, preview=False):
        self._ready(Action.EXEC_L1)
        argv = console.parse(raw)
        # A shared workspace must never silently change DB/credentials/protocol.
        if argv[0].upper() in {'SELECT', 'AUTH', 'HELLO', 'RESET', 'QUIT', 'MULTI', 'EXEC', 'DISCARD', 'WATCH', 'UNWATCH', 'MONITOR', 'READONLY', 'READWRITE', 'ASKING'}:
            raise AppError(ErrorCode.E08, '此工作区固定绑定连接和数据库，不支持切换会话状态；请另建连接。')
        if argv[0].upper() == 'CLIENT' and len(argv) > 1 and argv[1].upper() in {'REPLY', 'TRACKING', 'CACHING', 'NO-EVICT', 'NO-TOUCH'}:
            raise AppError(ErrorCode.E08, '共享工作区不允许改变连接回复或缓存模式。')
        return console.prepare(self.session, self.profile, raw, record_pending=not preview)

    def execute_confirmed(self, console, raw, confirmed=False, typed=''):
        gw = self._ready(Action.EXEC_L1)
        # Recheck permissions, policy and fail-closed audit immediately before the wire call.
        prepared = self.prepare_console(console, raw)
        decision = prepared.policy
        if decision.decision in {'confirm', 'typed_confirm'}:
            if not confirmed or (decision.requires_typed_confirm and typed != self.profile.name):
                raise AppError(ErrorCode.E08, '命令未确认，未发送。')
        self._ready(Action.EXEC_L1)
        started = time.perf_counter()
        try:
            result = console.execute(self.session, self.profile, prepared, gw)
            summary = ' | '.join(' '.join(row) for row in result.rows[:8]) if result.rows else ('OK' if result.ok else '')
            self._record_log(list(prepared.argv), result.ok, summary or f'{result.duration_ms}ms', result.duration_ms, 'console')
            return result
        except Exception as exc:
            ms = int((time.perf_counter() - started) * 1000)
            self._record_log(list(prepared.argv), False, getattr(exc, 'message', str(exc)), ms, 'console')
            raise

    def _drop_index(self):
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._index_path:
            self._index_path.unlink(missing_ok=True)
            self._index_path = None

    def disconnect(self):
        with self.lock:
            if self.gateway is not None:
                self.gateway.close()
                self.gateway = None
            self.dbsize = None
            self._drop_index()

    def close(self):
        with self.lock:
            self.disconnect()
            self.closed = True
