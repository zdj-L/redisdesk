"""Connection workspace: persistent key tree, tabbed details/console, and real execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal, Slot, Qt, QTimer, QSize
from PySide6.QtGui import QKeySequence, QShortcut, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QPlainTextEdit, QSplitter, QTreeWidget, QTreeWidgetItem, QTabWidget,
    QStackedWidget, QInputDialog, QSizePolicy, QAbstractItemView,
)

from redisdesk.app.services.workspace_session import WorkspaceSession, display_bytes
from redisdesk.app.ui.dialogs import ConfirmRiskDialog
from redisdesk.app.ui.i18n import env_label, protection_label, format_ttl, key_type_label
from redisdesk.app.ui.icons import make_icon, icon_size
from redisdesk.app.ui.theme import current_palette
from redisdesk.app.ui.value_views import StringViewer, FieldTableViewer
from redisdesk.app.ui.widgets import Badge
from redisdesk.core.model import ValuePage, ValueItem
from redisdesk.core.redact import format_argv_masked

ROLE_KIND = int(Qt.ItemDataRole.UserRole) + 1
ROLE_PREFIX = int(Qt.ItemDataRole.UserRole) + 2
ROLE_OFFSET = int(Qt.ItemDataRole.UserRole) + 3


class AsyncConnection(QObject):
    delivered = Signal(int, object, object)
    logged = Signal(object)

    def __init__(self, runtime):
        super().__init__()
        self.runtime = runtime
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='redis-workspace')
        self.callbacks = {}
        self.serial = 0
        self.closed = False
        self.futures = []
        self.delivered.connect(self._deliver)
        self.runtime.on_command_log = self._on_log

    def _on_log(self, entry):
        try:
            self.logged.emit(entry)
        except RuntimeError:
            pass

    def submit(self, operation, done, failed):
        if self.closed:
            return
        self.serial += 1
        ident = self.serial
        self.callbacks[ident] = (done, failed)
        future = self.executor.submit(operation, self.runtime)
        self.futures = [f for f in self.futures if not f.done()]
        self.futures.append(future)

        def settled(f):
            try:
                value, error = f.result(), None
            except BaseException as exc:
                value, error = None, exc
            try:
                self.delivered.emit(ident, value, error)
            except RuntimeError:
                pass

        future.add_done_callback(settled)

    @Slot(int, object, object)
    def _deliver(self, ident, value, error):
        callbacks = self.callbacks.pop(ident, None)
        if self.closed or callbacks is None:
            return
        callbacks[1](error) if error else callbacks[0](value)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.runtime.retired.set()
        self.runtime.on_command_log = None
        self.callbacks.clear()
        for future in self.futures:
            future.cancel()
        self.cleanup_future = self.executor.submit(lambda: self.runtime.close())
        self.executor.shutdown(wait=False)


def error_text(exc):
    return getattr(exc, 'message', str(exc))


def themed_icon(name, *, muted=False, size=16):
    tokens = current_palette()
    color = tokens['text_muted'] if muted else tokens['text_secondary']
    ratio = 1.0
    window = QGuiApplication.allWindows()
    if window:
        ratio = float(window[0].devicePixelRatio())
    return make_icon(name, color, size, pixel_ratio=max(1.0, ratio))


def quote_command(parts):
    rendered = []
    for part in parts:
        if isinstance(part, bytes):
            text = part.decode('utf-8')
        else:
            text = str(part)
        if text and all((ch.isprintable() and not ch.isspace() and ch not in '"\'') for ch in text):
            rendered.append(text)
        else:
            rendered.append('"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"')
    return ' '.join(rendered)


def can_quote(key: bytes) -> bool:
    try:
        quote_command([key])
        return True
    except UnicodeDecodeError:
        return False


class KeyBrowser(QWidget):
    key_selected = Signal(bytes)

    def __init__(self, io, profile, parent=None):
        super().__init__(parent)
        self.io = io
        self.profile = profile
        self.generation = 0
        self.busy = False
        self.auto = False
        self.page = 0
        self.scan_pattern = None
        self.last_value = None
        self.ready = False
        self.last_scan = None
        self.setMinimumWidth(280)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 4)
        root.setSpacing(4)
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.pattern = QLineEdit()
        self.pattern.setPlaceholderText('过滤，如 cache:*')
        self.pattern.setClearButtonEnabled(True)
        self.pattern.setMinimumWidth(80)
        self.refresh = QPushButton('刷新')
        self.more = QPushButton('加载更多')
        self.all = QPushButton('遍历到结束')
        self.cancel = QPushButton('取消')
        for widget in (self.refresh, self.more, self.all, self.cancel):
            widget.setObjectName('tool')
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
        bar.addWidget(self.pattern, 1)
        bar.addWidget(self.refresh)
        bar.addWidget(self.more)
        bar.addWidget(self.all)
        bar.addWidget(self.cancel)
        root.addLayout(bar)
        self.status = QLabel('连接后可扫描键名；点击叶子查看值。')
        self.status.setObjectName('hint')
        self.status.setWordWrap(False)
        self.status.setMaximumHeight(18)
        root.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setObjectName('keyTree')
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setIndentation(16)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.itemClicked.connect(self._choose)
        self.tree.itemExpanded.connect(self._expanded)
        root.addWidget(self.tree, 1)
        pages = QHBoxLayout()
        pages.setSpacing(6)
        self.prev = QPushButton('上一页')
        self.next = QPushButton('下一页')
        self.prev.setObjectName('tool')
        self.next.setObjectName('tool')
        self.page_label = QLabel('')
        self.page_label.setObjectName('muted')
        pages.addWidget(self.prev)
        pages.addWidget(self.next)
        pages.addWidget(self.page_label, 1)
        root.addLayout(pages)
        self.refresh.clicked.connect(self.start_scan)
        self.more.clicked.connect(self.scan_next)
        self.all.clicked.connect(self.scan_all)
        self.cancel.clicked.connect(self.cancel_scan)
        self.prev.clicked.connect(lambda: self.show_key_page(self.page - 1))
        self.next.clicked.connect(lambda: self.show_key_page(self.page + 1))
        self.pattern.returnPressed.connect(self.start_scan)
        self.value_more = QPushButton('下一页值')
        self.value_more.setObjectName('tool')
        self.value_more.setEnabled(False)
        self.value_more.hide()
        self.set_ready(False)

    def set_ready(self, ready):
        self.ready = ready
        for widget in (self.refresh, self.more, self.all, self.pattern):
            widget.setEnabled(ready)
        self.cancel.setEnabled(False)
        self.prev.setEnabled(False)
        self.next.setEnabled(False)
        if not ready:
            self.scan_pattern = None
            self.cancel_scan()
            self.tree.clear()
            self.last_value = None
            self.last_scan = None
            self.status.setText('尚未连接此工作区。')

    def start_scan(self, checked=False, *, auto=False):
        if not self.ready:
            return
        self.cancel_scan()
        self.auto = auto
        self.last_value = None
        generation = self.generation
        self.tree.clear()
        self.page = 0
        pattern = self.pattern.text().strip() or '*'
        self.scan_pattern = pattern
        self.busy = True
        self.cancel.setEnabled(True)
        self.status.setText('正在扫描第一批键…')
        def work(runtime):
            runtime.reset_scan(pattern)
            return runtime.scan_next()
        self.io.submit(work, lambda result: self._scanned(generation, result), lambda exc: self._scan_error(generation, exc))

    def scan_next(self):
        if self.busy or not self.ready:
            return
        if self.scan_pattern != (self.pattern.text().strip() or '*'):
            self.start_scan(auto=self.auto)
            return
        if self.scan_pattern is None:
            self.start_scan(auto=self.auto)
            return
        self.busy = True
        self.cancel.setEnabled(True)
        generation = self.generation
        self.status.setText('正在扫描下一批…')
        self.io.submit(lambda runtime: runtime.scan_next(), lambda result: self._scanned(generation, result), lambda exc: self._scan_error(generation, exc))

    def scan_all(self):
        self.auto = True
        self.scan_next()

    def _scanned(self, generation, result):
        if generation != self.generation:
            return
        self.busy = False
        self.render_page(result)
        self.cancel.setEnabled(self.auto)
        if self.auto and not result['complete']:
            QTimer.singleShot(0, lambda: self.scan_next() if self.auto and generation == self.generation else None)
        else:
            self.auto = False

    def _scan_error(self, generation, exc):
        if generation != self.generation:
            return
        self.busy = False
        self.auto = False
        self.cancel.setEnabled(False)
        self.status.setText('扫描失败，可重试：' + error_text(exc))

    def cancel_scan(self):
        self.auto = False
        self.busy = False
        self.generation += 1
        self.cancel.setEnabled(False)
        if self.ready:
            self.status.setText('扫描已取消；正在进行的一次请求会在超时内结束。可继续扫描。')

    def show_key_page(self, page):
        if not self.ready or self.busy:
            return
        generation = self.generation
        self.io.submit(
            lambda runtime: runtime.key_page(max(0, page)),
            lambda result: self.render_page(result) if generation == self.generation else None,
            lambda exc: self.status.setText(error_text(exc)),
        )

    def _make_item(self, title, kind, icon, identity=None, prefix=None, tooltip=''):
        item = QTreeWidgetItem([title])
        item.setIcon(0, themed_icon(icon, muted=(kind == 'folder')))
        item.setData(0, ROLE_KIND, kind)
        if identity is not None:
            item.setData(0, Qt.ItemDataRole.UserRole, identity)
        if prefix is not None:
            item.setData(0, ROLE_PREFIX, prefix)
        if tooltip:
            item.setToolTip(0, tooltip)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item

    def render_page(self, result):
        self.last_scan = result
        self.page = result['page']
        self.tree.clear()
        profile = self.profile
        conn = self._make_item(profile.name, 'connection', 'server', tooltip=f'{profile.host}:{profile.port}')
        discovered = result['total']
        size = result.get('dbsize')
        db_text = f'db{profile.db}  ({discovered}' + (f' / {size}' if size is not None else '') + ')'
        db = self._make_item(db_text, 'db', 'database', tooltip='已发现键数；完成扫描前不是全库快照')
        conn.addChild(db)
        self.tree.addTopLevelItem(conn)
        tree = result.get('tree') if result.get('tree_mode') == 'full' else None
        if tree is not None:
            self._fill_full(db, tree)
        else:
            self._fill_lazy(db, result.get('namespace') or {'folders': [], 'leaves': [], 'more': False, 'offset': 0, 'leaf_total': 0, 'prefix': None})
        conn.setExpanded(True)
        db.setExpanded(True)
        if tree is not None:
            self._expand_folders(db)
        total = result['total']
        self.prev.setEnabled(self.page > 0)
        self.next.setEnabled((self.page + 1) * 1000 < total)
        extra = '' if result.get('tree_mode') == 'full' or total <= 1000 else f' · 第 {self.page + 1} 页'
        self.page_label.setText(f'已发现 {total} 键' + extra)
        state = '本轮游标已结束（动态库非快照）' if result['complete'] else '尚未遍历完，可继续扫描'
        self.status.setText(f'已去重发现 {total} 个键；{state}')
        self.more.setEnabled(not result['complete'])
        self.all.setEnabled(not result['complete'])

    def _fill_full(self, parent, node):
        for folder in node.get('children') or []:
            title = f"{display_bytes(folder['segment'])[:80] or '(空段)'}  ({folder['count']})"
            item = self._make_item(title, 'folder', 'folder', prefix=folder['prefix'], tooltip=title)
            parent.addChild(item)
            self._fill_full(item, folder.get('child') or folder)
        for key in node.get('leaves') or []:
            parent.addChild(self._leaf_item(key))

    def _fill_lazy(self, parent, node):
        for folder in node.get('folders') or []:
            title = f"{display_bytes(folder['segment'])[:80] or '(空段)'}  ({folder['count']})"
            item = self._make_item(title, 'folder', 'folder', prefix=folder['prefix'], tooltip=title)
            placeholder = QTreeWidgetItem(['加载中…'])
            placeholder.setData(0, ROLE_KIND, 'placeholder')
            item.addChild(placeholder)
            parent.addChild(item)
        for key in node.get('leaves') or []:
            parent.addChild(self._leaf_item(key))
        if node.get('more'):
            more = self._make_item('加载更多键…', 'more', 'empty', prefix=node.get('prefix'), tooltip='继续加载此目录下的键')
            more.setData(0, ROLE_OFFSET, int(node.get('offset', 0) + len(node.get('leaves') or [])))
            parent.addChild(more)

    def _leaf_item(self, key: bytes):
        label = display_bytes(key.split(b':')[-1] if b':' in key else key)
        item = self._make_item(
            label[:150] or '(空键名)',
            'leaf',
            'key',
            identity=key,
            tooltip=display_bytes(key)[:1000] + '\n原始字节 HEX: ' + key[:256].hex(),
        )
        return item

    def _expand_folders(self, item):
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, ROLE_KIND) == 'folder':
                child.setExpanded(True)
                self._expand_folders(child)

    def _expanded(self, item):
        if not self.ready or item.data(0, ROLE_KIND) != 'folder':
            return
        if item.childCount() == 1 and item.child(0).data(0, ROLE_KIND) == 'placeholder':
            prefix = item.data(0, ROLE_PREFIX)
            generation = self.generation
            self.io.submit(
                lambda runtime: runtime.tree_children(prefix, 0, 400),
                lambda result: self._loaded_children(item, result, generation),
                lambda exc: self.status.setText(error_text(exc)),
            )

    def _loaded_children(self, item, result, generation):
        if generation != self.generation or item.treeWidget() is None:
            return
        item.takeChildren()
        self._fill_lazy(item, result)
        item.setExpanded(True)

    def _choose(self, item, column=0):
        kind = item.data(0, ROLE_KIND)
        if kind == 'more' and self.ready:
            prefix = item.data(0, ROLE_PREFIX)
            offset = int(item.data(0, ROLE_OFFSET) or 0)
            parent = item.parent()
            generation = self.generation
            self.io.submit(
                lambda runtime: runtime.tree_children(prefix, offset, 400),
                lambda result: self._append_more(parent, item, result, generation),
                lambda exc: self.status.setText(error_text(exc)),
            )
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(key, bytes) and self.ready:
            self.key_selected.emit(key)

    def _append_more(self, parent, item, result, generation):
        if generation != self.generation or parent is None:
            return
        parent.removeChild(item)
        self._fill_lazy(parent, {**result, 'folders': []})

    def collect_leaves(self):
        leaves = []

        def walk(item):
            value = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(value, bytes):
                leaves.append(value)
            for i in range(item.childCount()):
                walk(item.child(i))

        walk(self.tree.invisibleRootItem())
        return leaves


class KeyDetail(QWidget):
    request_command = Signal(str)

    def __init__(self, io, parent=None):
        super().__init__(parent)
        self.io = io
        self.value_generation = 0
        self.last_value = None
        self.history = []
        self.ready = False
        self.current_key = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 4)
        root.setSpacing(4)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        empty = QWidget()
        empty_l = QVBoxLayout(empty)
        empty_l.setContentsMargins(24, 24, 24, 24)
        self.empty = QLabel('请选择键')
        self.empty.setObjectName('keyEmpty')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        empty_l.addStretch()
        empty_l.addWidget(self.empty)
        empty_l.addStretch()
        body = QWidget()
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(8)
        head = QHBoxLayout()
        self.key_label = QLabel('')
        self.key_label.setObjectName('sectionTitle')
        self.key_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.key_label.setWordWrap(True)
        head.addWidget(self.key_label, 1)
        self.type_badge = Badge('—', 'muted')
        self.ttl_badge = Badge('TTL —', 'muted')
        self.size_label = QLabel('')
        self.size_label.setObjectName('muted')
        head.addWidget(self.type_badge)
        head.addWidget(self.ttl_badge)
        head.addWidget(self.size_label)
        body_l.addLayout(head)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        self.reload = QPushButton('刷新')
        self.copy = QPushButton('复制键名')
        self.ttl_btn = QPushButton('设置 TTL')
        self.rename_btn = QPushButton('重命名')
        self.delete_btn = QPushButton('删除')
        for widget in (self.reload, self.copy, self.ttl_btn, self.rename_btn, self.delete_btn):
            widget.setObjectName('tool')
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(widget)
        self.hash_add = QPushButton('新增字段')
        self.hash_del = QPushButton('删除选中字段')
        for widget in (self.hash_add, self.hash_del):
            widget.setObjectName('tool')
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
            widget.hide()
            actions.addWidget(widget)
        self.string_more = QPushButton('加载下一块')
        self.string_more.setObjectName('tool')
        self.string_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.string_more.hide()
        actions.addWidget(self.string_more)
        actions.addStretch()
        body_l.addLayout(actions)
        self.views = QStackedWidget()
        self.views.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.string_view = StringViewer()
        self.hash_view = FieldTableViewer(['字段', '值'], '该哈希的字段将按页显示。')
        self.list_view = FieldTableViewer(['索引', '值'], '列表元素按页显示。')
        self.set_view = FieldTableViewer(['成员'], '集合成员按页显示。')
        self.zset_view = FieldTableViewer(['成员', '分数'], '有序集合成员按页显示。')
        self.stream_view = FieldTableViewer(['消息 ID', '内容'], '流消息按 ID 分页显示。')
        for view in (self.string_view, self.hash_view, self.list_view, self.set_view, self.zset_view, self.stream_view):
            view.layout().setContentsMargins(0, 0, 0, 0)
            self.views.addWidget(view)
        body_l.addWidget(self.views, 1)
        self.value_prev = QPushButton('上一页值')
        self.value_more = QPushButton('下一页 / 下一块')
        self.value_prev.setObjectName('tool')
        self.value_more.setObjectName('tool')
        self.value_prev.hide()
        self.value_more.hide()
        self.value_prev.setEnabled(False)
        self.value_more.setEnabled(False)
        self.stack.addWidget(empty)
        self.stack.addWidget(body)
        root.addWidget(self.stack, 1)
        self.reload.clicked.connect(self.refresh)
        self.copy.clicked.connect(self.copy_key)
        self.ttl_btn.clicked.connect(self.edit_ttl)
        self.rename_btn.clicked.connect(self.rename_key)
        self.delete_btn.clicked.connect(self.delete_key)
        self.hash_add.clicked.connect(self.add_hash_field)
        self.hash_del.clicked.connect(self.delete_hash_field)
        self.string_more.clicked.connect(self.next_value)
        self.string_view.more.clicked.connect(self.next_value)
        for view in (self.hash_view, self.list_view, self.set_view, self.zset_view, self.stream_view):
            view.next.clicked.connect(self.next_value)
            view.prev.clicked.connect(self.prev_value)
        self.set_ready(False)
        self.show_empty('请选择键')

    def set_ready(self, ready):
        self.ready = ready
        if not ready:
            self.value_generation += 1
            self.current_key = None
            self.last_value = None
            self.history = []
            self.string_more.hide()
            self.show_empty('请先连接此工作区')

    def show_empty(self, text):
        self.empty.setText(text)
        self.stack.setCurrentIndex(0)
        self.value_more.setEnabled(False)
        self.value_prev.setEnabled(False)
        self.value_more.hide()
        self.value_prev.hide()
        if hasattr(self, 'string_more'):
            self.string_more.hide()

    def open_key(self, key: bytes):
        if not self.ready:
            self.show_empty('请先连接此工作区')
            return
        self.current_key = key
        self.history = []
        self.value_generation += 1
        self.load_value(key, 0, None, replace=True)

    def load_value(self, key, token, kind, *, replace=False):
        generation = self.value_generation
        self.value_more.setEnabled(False)
        self.io.submit(
            lambda runtime: runtime.read_value(key, token, kind),
            lambda result: self.render_value(result, replace=replace) if generation == self.value_generation else None,
            lambda exc: self._failed(error_text(exc)) if generation == self.value_generation else None,
        )

    def _failed(self, message):
        self.show_empty('读取失败：' + message)

    def render_value(self, result, *, replace=False):
        if replace:
            self.history = [result]
        else:
            self.history.append(result)
        self.last_value = result
        self.stack.setCurrentIndex(1)
        key_text = display_bytes(result['key'])
        self.key_label.setText(key_text)
        self.key_label.setToolTip(key_text)
        label = key_type_label(result['kind']) if result['kind'] not in {'none'} else result['kind']
        self.type_badge.apply('info', label)
        self.ttl_badge.apply('muted', 'TTL ' + format_ttl(result['ttl']))
        kind = result['kind']
        total = result['total']
        self.size_label.setText('' if total is None else (f'{total} 字节' if kind == 'string' else f'{total} 项'))
        writable = can_quote(result['key'])
        for widget in (self.ttl_btn, self.rename_btn, self.delete_btn, self.hash_add, self.hash_del):
            widget.setEnabled(writable and self.ready)
        self.hash_add.setVisible(kind == 'hash')
        self.hash_del.setVisible(kind == 'hash')
        self.ttl_btn.setToolTip('' if writable else '二进制键请通过控制台操作')
        index = {'string': 0, 'hash': 1, 'list': 2, 'set': 3, 'zset': 4, 'stream': 5}.get(kind, 0)
        self.views.setCurrentIndex(index)
        rows = [list(row) for row in result['rows']]
        page_index = max(0, len(self.history) - 1)
        if kind == 'string':
            item = result['rows'][0][0] if result['rows'] else ''
            page = ValuePage(
                key=key_text,
                key_type='string',
                page_index=page_index,
                offset_or_cursor=str(result.get('token') or 0),
                items=(ValueItem(field=None, value_text=item, value_bytes_len=total or len(str(item))),),
                total=total,
                has_more=result['more'],
            )
            self.string_view.show_page(page)
        elif kind == 'hash':
            self.hash_view.show_page(rows, total=total, has_more=result['more'], page_index=page_index)
        elif kind == 'list':
            self.list_view.show_page(rows, total=total, has_more=result['more'], page_index=page_index)
        elif kind == 'set':
            self.set_view.show_page(rows, total=total, has_more=result['more'], page_index=page_index)
        elif kind == 'zset':
            self.zset_view.show_page(rows, total=total, has_more=result['more'], page_index=page_index)
        else:
            self.stream_view.show_page(rows, total=total, has_more=result['more'], page_index=page_index)
        # Collection paging lives on the table viewer; strings use a compact toolbar button.
        self.value_more.hide()
        self.value_prev.hide()
        self.value_more.setEnabled(False)
        self.value_prev.setEnabled(False)
        more = bool(result['more'] and kind == 'string')
        self.string_more.setVisible(more)
        self.string_more.setEnabled(more and self.ready)

    def next_value(self):
        if self.last_value and self.last_value['more']:
            r = self.last_value
            self.load_value(r['key'], r['token'], r['kind'])

    def prev_value(self):
        if len(self.history) > 1:
            self.history.pop()
            self.render_value(self.history.pop(), replace=False)

    def refresh(self):
        if self.current_key:
            self.open_key(self.current_key)

    def copy_key(self):
        if self.current_key is None:
            return
        QGuiApplication.clipboard().setText(display_bytes(self.current_key))

    def edit_ttl(self):
        if not self.current_key or not can_quote(self.current_key):
            return
        value, ok = QInputDialog.getInt(self, '设置 TTL', '过期秒数（0 表示删除 TTL，键永久保留）', 60, 0, 60 * 60 * 24 * 365, 1)
        if not ok:
            return
        key = quote_command([self.current_key])
        if value <= 0:
            self.request_command.emit(f'PERSIST {key}')
        else:
            self.request_command.emit(f'EXPIRE {key} {value}')

    def rename_key(self):
        if not self.current_key or not can_quote(self.current_key):
            return
        value, ok = QInputDialog.getText(self, '重命名', '新的键名', text=display_bytes(self.current_key))
        if not ok or not value.strip():
            return
        self.request_command.emit(f'RENAME {quote_command([self.current_key])} {quote_command([value.strip()])}')

    def delete_key(self):
        if not self.current_key or not can_quote(self.current_key):
            return
        self.request_command.emit(f'DEL {quote_command([self.current_key])}')

    def add_hash_field(self):
        if not self.current_key or not can_quote(self.current_key) or not self.last_value or self.last_value['kind'] != 'hash':
            return
        field, ok = QInputDialog.getText(self, '新增字段', '字段名')
        if not ok or field == '':
            return
        value, ok = QInputDialog.getText(self, '新增字段', '字段值')
        if not ok:
            return
        self.request_command.emit(f'HSET {quote_command([self.current_key])} {quote_command([field])} {quote_command([value])}')

    def delete_hash_field(self):
        if not self.current_key or not can_quote(self.current_key) or not self.last_value or self.last_value['kind'] != 'hash':
            return
        row = self.hash_view.table.currentRow()
        item = self.hash_view.table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        self.request_command.emit(f'HDEL {quote_command([self.current_key])} {quote_command([item.text()])}')


class WorkspaceConsole(QWidget):
    def __init__(self, io, console, parent=None):
        super().__init__(parent)
        self.io, self.console = io, console
        self.ready = False
        self.busy = False
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel('控制台')
        title.setObjectName('sectionTitle')
        self.clear = QPushButton('清空输出')
        self.clear.setObjectName('tool')
        head.addWidget(title)
        head.addStretch()
        head.addWidget(self.clear)
        root.addLayout(head)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setObjectName('consoleOutput')
        self.output.setPlaceholderText('命令结果会显示在这里。')
        root.addWidget(self.output, 1)
        bar = QWidget()
        bar.setObjectName('consoleBar')
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText('输入 Redis 命令，例如 GET cache:a。Ctrl+Enter 执行。')
        self.input.setFixedHeight(56)
        self.input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.run = QPushButton('执行')
        self.run.setObjectName('consoleRun')
        self.run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run.setDefault(True)
        self.run.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.run.setMaximumWidth(96)
        hint = QLabel('Ctrl+Enter')
        hint.setObjectName('muted')
        row.addWidget(self.input, 1)
        row.addWidget(self.run, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(hint, 0, Qt.AlignmentFlag.AlignBottom)
        root.addWidget(bar)
        self.run.clicked.connect(self.execute)
        self.clear.clicked.connect(self.output.clear)
        QShortcut(QKeySequence('Ctrl+Return'), self, activated=self.execute)
        QShortcut(QKeySequence('Ctrl+Enter'), self, activated=self.execute)
        self.set_ready(False)

    def set_ready(self, ready):
        self.ready = ready
        self.run.setEnabled(ready and not self.busy)
        self.input.setEnabled(ready)

    def append(self, text):
        self.output.appendPlainText(text)
        self.output.document().setMaximumBlockCount(1000)

    def execute(self, raw=None):
        if raw is None or isinstance(raw, bool):
            raw = self.input.toPlainText().strip()
        if not raw or not self.ready or self.busy:
            return
        self.busy = True
        self.run.setEnabled(False)
        def prepare(runtime):
            return runtime.prepare_console(self.console, raw, preview=True)
        self.io.submit(prepare, lambda prepared: self._review(raw, prepared), self._error)

    def _review(self, raw, prepared):
        if not self.ready:
            self._settled()
            return
        decision = prepared.policy
        confirmed = False
        typed = ''
        if decision.decision in {'confirm', 'typed_confirm'}:
            profile = self.io.runtime.profile
            dlg = ConfirmRiskDialog(
                title=f'确认在「{profile.name}」执行',
                command=format_argv_masked(list(prepared.argv)),
                consequence=decision.assessment.reason,
                typed_text=profile.name,
                require_typed=decision.requires_typed_confirm,
                parent=self,
            )
            if not dlg.exec():
                self.append('已取消，命令未发送。')
                self._settled()
                return
            confirmed = True
            typed = dlg.typed.text() if decision.requires_typed_confirm else ''
        self.append('$ ' + format_argv_masked(list(prepared.argv)))
        self.io.submit(lambda runtime: runtime.execute_confirmed(self.console, raw, confirmed, typed), self._result, self._error)

    def _result(self, result):
        rows = '\n'.join(' | '.join(row) for row in result.rows)
        tone = '成功' if result.ok else '失败'
        self.append(f'{tone}  [{result.argv_name}  {result.duration_ms} ms]\n' + (rows[:65536] or '(空结果)'))
        self._settled()

    def _error(self, exc):
        self.append('失败 / 未执行成功：' + error_text(exc))
        self._settled()

    def _settled(self):
        self.busy = False
        self.run.setEnabled(self.ready)


class CommandLog(QWidget):
    expanded = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 0, 8, 4)
        root.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(0, 2, 0, 2)
        self.toggle = QPushButton('操作日志')
        self.toggle.setObjectName('logToggle')
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setFlat(True)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setMaximumHeight(24)
        self.meta = QLabel('')
        self.meta.setObjectName('muted')
        head.addWidget(self.toggle)
        head.addWidget(self.meta, 1)
        root.addLayout(head)
        self.body = QPlainTextEdit()
        self.body.setObjectName('commandLog')
        self.body.setReadOnly(True)
        self.body.setMinimumHeight(80)
        self.body.setPlaceholderText('此工作区实际发出的命令会记录在这里。')
        self.body.hide()
        root.addWidget(self.body, 1)
        self.toggle.toggled.connect(self._on_toggle)

    def is_expanded(self):
        return self.toggle.isChecked()

    def _on_toggle(self, checked):
        self.body.setVisible(checked)
        self.expanded.emit(checked)

    def minimumSizeHint(self):
        header = 28
        if self.body.isVisible():
            return QSize(200, header + 80)
        return QSize(200, header)

    def sizeHint(self):
        return self.minimumSizeHint()

    def append_entry(self, entry):
        argv = ' '.join(str(part) for part in entry.get('argv') or [])
        status = 'OK' if entry.get('ok') else 'ERR'
        line = f"{status}  {argv}  {entry.get('ms', 0)}ms  {entry.get('summary') or ''}"
        self.body.appendPlainText(line.strip())
        self.body.document().setMaximumBlockCount(400)
        self.meta.setText(f"{self.body.document().blockCount()} 条")


class ConnectionWorkspace(QWidget):
    state_changed = Signal(str, object)

    def __init__(self, service, console, session, profile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.setObjectName('workspaceRoot')
        self.io = AsyncConnection(WorkspaceSession(service, session, profile))
        self.closed = False
        self.state = 'disconnected'
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QWidget()
        header.setObjectName('workspaceHeader')
        bar = QHBoxLayout(header)
        bar.setContentsMargins(10, 4, 10, 4)
        bar.setSpacing(8)
        self.identity = QLabel(f'{profile.name}  ·  db{profile.db}')
        self.identity.setObjectName('workspaceMeta')
        self.host_label = QLabel(f'{profile.host}:{profile.port}')
        self.host_label.setObjectName('muted')
        self.state_badge = Badge('未连接', 'muted')
        self.env_badge = Badge(env_label(profile.env), 'prod' if profile.env == 'prod' else 'info')
        self.protect_badge = Badge(protection_label(profile.protection), 'warn' if profile.protection != 'readonly' else 'muted')
        self.connect_btn = QPushButton('连接')
        self.disconnect_btn = QPushButton('断开')
        self.connect_btn.setObjectName('tool')
        self.disconnect_btn.setObjectName('tool')
        bar.addWidget(self.identity)
        bar.addWidget(self.host_label)
        bar.addWidget(self.state_badge)
        bar.addWidget(self.env_badge)
        bar.addWidget(self.protect_badge)
        bar.addStretch()
        bar.addWidget(self.connect_btn)
        bar.addWidget(self.disconnect_btn)
        root.addWidget(header)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setObjectName('workspaceHSplit')
        self.split.setChildrenCollapsible(False)
        self.browser = KeyBrowser(self.io, profile)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('workspaceTabs')
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.detail = KeyDetail(self.io)
        self.console = WorkspaceConsole(self.io, console)
        self._detail_index = self.tabs.addTab(self.detail, '键详情')
        self._console_index = self.tabs.addTab(self.console, '控制台')
        self.tabs.tabBar().setTabButton(self._detail_index, self.tabs.tabBar().ButtonPosition.RightSide, None)
        self.tabs.tabBar().setTabButton(self._console_index, self.tabs.tabBar().ButtonPosition.RightSide, None)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.browser.key_selected.connect(self.open_key)
        self.detail.request_command.connect(self.run_command)
        self.split.addWidget(self.browser)
        self.split.addWidget(self.tabs)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 2)
        self.split.setSizes([380, 700])
        self.vsplit = QSplitter(Qt.Orientation.Vertical)
        self.vsplit.setObjectName('workspaceVSplit')
        self.vsplit.setChildrenCollapsible(False)
        self.vsplit.addWidget(self.split)
        self.command_log = CommandLog()
        self.vsplit.addWidget(self.command_log)
        self.vsplit.setStretchFactor(0, 3)
        self.vsplit.setStretchFactor(1, 1)
        root.addWidget(self.vsplit, 1)
        self.io.logged.connect(self.command_log.append_entry)
        self.command_log.expanded.connect(self._on_log_toggled)
        self.split.splitterMoved.connect(self._mark_h_split_adjusted)
        self.vsplit.splitterMoved.connect(self._mark_v_split_adjusted)
        self._user_adjusted_h = False
        self._user_adjusted_v = False
        self.connect_btn.clicked.connect(self.start_connect)
        self.disconnect_btn.clicked.connect(self.start_disconnect)
        self.browser.value_more = self.detail.value_more
        self.start_connect()

    def open_key(self, key: bytes):
        self.tabs.setCurrentWidget(self.detail)
        self.detail.open_key(key)
        self.tabs.setTabText(self._detail_index, display_bytes(key)[:24] or '键详情')
        self.tabs.setTabToolTip(self._detail_index, display_bytes(key))

    def show_console(self):
        self.tabs.setCurrentWidget(self.console)

    def _close_tab(self, index):
        widget = self.tabs.widget(index)
        if widget in {self.detail, self.console}:
            return
        self.tabs.removeTab(index)
        if widget is not None:
            widget.deleteLater()

    def run_command(self, raw: str):
        self.show_console()
        self.console.input.setPlainText(raw)
        self.console.execute(raw)

    def _mark_h_split_adjusted(self, *_args):
        self._user_adjusted_h = True

    def _mark_v_split_adjusted(self, *_args):
        self._user_adjusted_v = True

    def _tree_default_width(self, total):
        if total <= 0:
            return 380
        tree = int(round(total * 0.34))
        return min(420, max(280, tree))

    def _apply_horizontal_default(self):
        width = self.split.width()
        if width <= 0:
            return
        tree = self._tree_default_width(width)
        rest = width - tree
        if rest < 280:
            tree = max(280, width - 280)
        self.split.setSizes([tree, max(width - tree, 1)])

    def _apply_vertical_default(self):
        height = self.vsplit.height()
        if height <= 0:
            return
        if self.command_log.is_expanded():
            log = min(120, max(80, int(round(height * 0.22))))
        else:
            log = max(self.command_log.minimumSizeHint().height(), 28)
        self.vsplit.setSizes([max(height - log, 80), log])

    def _on_log_toggled(self, checked):
        self._user_adjusted_v = False
        self._apply_vertical_default()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._user_adjusted_h:
            self._apply_horizontal_default()
        if not self._user_adjusted_v:
            self._apply_vertical_default()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._user_adjusted_h:
            self._apply_horizontal_default()
        if not self._user_adjusted_v:
            self._apply_vertical_default()

    def start_connect(self):
        if self.state in {'connecting', 'connected', 'disconnecting'} or self.closed:
            return
        self.state = 'connecting'
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText('正在连接')
        self.state_badge.apply('muted', '连接中')
        self.state_changed.emit(self.state, self.profile)
        self.io.submit(lambda runtime: runtime.connect(), self._connected, self._failed)

    def _connected(self, profile):
        if self.state != 'connecting':
            return
        self.state = 'connected'
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText('连接')
        self.disconnect_btn.setEnabled(True)
        self.state_badge.apply('success', '已连接')
        self.browser.set_ready(True)
        self.console.set_ready(True)
        self.detail.set_ready(True)
        self.detail.show_empty('请选择键')
        self.browser.start_scan()
        self.state_changed.emit(self.state, self.profile)

    def _failed(self, exc):
        self.state = 'disconnected'
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText('重试')
        self.disconnect_btn.setEnabled(False)
        self.state_badge.apply('danger', '失败')
        self.browser.set_ready(False)
        self.console.set_ready(False)
        self.detail.set_ready(False)
        self.detail.show_empty('连接失败：' + error_text(exc))
        self.state_changed.emit(self.state, self.profile)

    def start_disconnect(self):
        if self.closed or self.state == 'disconnecting':
            return
        self.state = 'disconnecting'
        self.connect_btn.setEnabled(False)
        self.browser.set_ready(False)
        self.console.set_ready(False)
        self.detail.set_ready(False)
        self.state_badge.apply('muted', '断开中')
        self.state_changed.emit(self.state, self.profile)
        self.io.submit(lambda runtime: runtime.disconnect(), self._disconnected, self._failed)

    def _disconnected(self, _):
        self.state = 'disconnected'
        self.state_badge.apply('muted', '已断开')
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText('连接')
        self.state_changed.emit(self.state, self.profile)

    def close_workspace(self):
        if self.closed:
            return
        self.closed = True
        self.browser.set_ready(False)
        self.console.set_ready(False)
        self.detail.set_ready(False)
        self.io.close()
        self.state = 'disconnected'
        self.state_changed.emit(self.state, self.profile)
