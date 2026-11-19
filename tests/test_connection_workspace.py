"""Real redis-py against an isolated in-process RESP server; never touches user's Redis."""
from contextlib import contextmanager
from dataclasses import replace
import socketserver
import threading
import time
import os
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint

from redisdesk.app.services.connection_service import ConnectionService
from redisdesk.app.services.console_service import ConsoleService
from redisdesk.app.services.workspace_session import WorkspaceSession
from redisdesk.app.ui.connection_workspace import ConnectionWorkspace
from redisdesk.app.ui.pages.connections import ConnectionsPage
from redisdesk.app.ui.theme import apply_theme
from redisdesk.core.authz import StaticGrantResolver
from redisdesk.core.model import Endpoint
from redisdesk.core.errors import AppError
from tests.conftest import make_profile, make_session


def resp(value):
    if value is None: return b'$-1\r\n'
    if isinstance(value,int): return b':'+str(value).encode()+b'\r\n'
    if isinstance(value,(list,tuple)): return b'*'+str(len(value)).encode()+b'\r\n'+b''.join(resp(x) for x in value)
    if isinstance(value,str): value=value.encode()
    return b'$'+str(len(value)).encode()+b'\r\n'+value+b'\r\n'

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            line=self.rfile.readline()
            if not line:return
            n=int(line[1:]);parts=[]
            for _ in range(n):
                size=int(self.rfile.readline()[1:]);parts.append(self.rfile.read(size));self.rfile.read(2)
            self.server.commands.append(parts)
            cmd=parts[0].upper()
            if cmd==b'CLIENT':
                self.wfile.write(b'-ERR unknown command CLIENT\r\n');self.wfile.flush();continue
            if cmd in {b'SELECT', b'AUTH'}:value='OK'
            elif cmd==b'PING':value='PONG'
            elif cmd==b'INFO':value='# Server\r\nredis_version:7.2.0\r\n'
            elif cmd==b'DBSIZE':value=6
            elif cmd==b'SCAN':
                value=[b'7',[b'cache:a', b'cache', b'cache:a:1', b'\xff:raw', b'h']] if parts[1]==b'0' else [b'0',[b'cache:a',b'other', b'h']]
            elif cmd==b'TYPE':
                value={b'h':'hash',b'l':'list',b's':'set',b'z':'zset',b'x':'stream',b'gone':'none'}.get(parts[1],'string')
            elif cmd==b'TTL':value=60
            elif cmd==b'STRLEN':value=len(getattr(self.server,'string_value',b'value'))
            elif cmd==b'GET':
                if parts[1]==b'fail':
                    self.wfile.write(b'-ERR simulated failure\r\n');self.wfile.flush();continue
                value=getattr(self.server,'string_value',b'value')
            elif cmd==b'GETRANGE':value=getattr(self.server,'string_value',b'value')[int(parts[2]):int(parts[3])+1]
            elif cmd==b'LLEN':value=len(getattr(self.server,'list_values',[b'a',b'b']))
            elif cmd in {b'HLEN',b'SCARD',b'ZCARD',b'XLEN'}:
                store=getattr(self.server,'hashes',None)
                if cmd==b'HLEN' and store is not None and parts[1] in store:
                    value=len(store[parts[1]])
                else:
                    value=2
            elif cmd==b'HSCAN':
                store=getattr(self.server,'hashes',None)
                if store is not None and parts[1] in store and not getattr(self.server,'hash_pages',False):
                    flat=[]
                    for field,val in store[parts[1]].items():
                        flat.extend([field,val])
                    value=[b'0',flat]
                elif getattr(self.server,'hash_pages',False):
                    value=[b'17',sum(([('f'+str(i)).encode(),b'value'] for i in range(151)),[])] if parts[2]==b'0' else [b'0',[b'last',b'value']]
                else:value=[b'0',[b'field',b'value']]
            elif cmd==b'HSET':
                if not hasattr(self.server,'hashes') or self.server.hashes is None:
                    self.server.hashes={}
                bucket=self.server.hashes.setdefault(parts[1],{})
                created=0 if parts[2] in bucket else 1
                bucket[parts[2]]=parts[3]
                value=created
            elif cmd==b'SSCAN':value=[b'0',[b'a',b'b']]
            elif cmd==b'ZSCAN':value=[b'0',[b'a',b'1',b'b',b'2']]
            elif cmd==b'LRANGE':value=getattr(self.server,'list_values',[b'a',b'b'])[int(parts[2]):int(parts[3])+1]
            elif cmd==b'XRANGE':
                entries=getattr(self.server,'stream_values',[[b'1-0',[b'field',b'value']]])
                offset=0 if parts[2]==b'-' else int(parts[2].split(b'-')[1])
                value=entries[offset:offset+100]
            elif cmd==b'SET':value='OK'  # writes stay only in this isolated test server
            else:
                self.wfile.write(b'-NOPERM command denied\r\n');self.wfile.flush();continue
            self.wfile.write(resp(value));self.wfile.flush()

@contextmanager
def server_context():
    with Server(('127.0.0.1',0),Handler) as server:
        server.commands=[]
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:yield server
        finally:server.shutdown();thread.join(timeout=3)

@pytest.fixture
def wire():
    with server_context() as server:yield server

@pytest.fixture
def services(tmp_store,audit,wire):
    profile=replace(make_profile(tls=False),endpoint=Endpoint('127.0.0.1',wire.server_address[1]))
    grants=StaticGrantResolver({('c1','admin'),('c2','admin')})
    connections=ConnectionService(tmp_store,audit,grants)
    console=ConsoleService(tmp_store,audit,grants)
    return connections,console,make_session(),profile

@pytest.fixture
def runtime(services):
    connections,console,session,profile=services
    runtime=WorkspaceSession(connections,session,profile)
    runtime.connect()
    try:yield runtime
    finally:runtime.close()


def test_session_scan_and_lazy_get_real_wire(runtime,wire):
    assert runtime.gateway._client.connection_pool.connection_kwargs['protocol']==2
    runtime.reset_scan('cache:*')
    first=runtime.scan_next()
    assert not first['complete'] and first['total']==5
    assert not any(parts[0] in {b'GET',b'TYPE',b'TTL'} for parts in wire.commands)
    second=runtime.scan_next()
    assert second['complete'] and second['total']==6
    assert len(second['keys'])==len(set(second['keys']))
    value=runtime.read_value(b'cache:a')
    assert value['rows']==[('value',)] and value['ttl']==60
    assert [b'GET',b'cache:a'] in wire.commands
    runtime.read_value(b'\xff:raw')
    assert [b'GET',b'\xff:raw'] in wire.commands
    assert not any(parts[0] in {b'KEYS',b'HELLO'} for parts in wire.commands)
    path=runtime._index_path
    runtime.disconnect()
    assert not path.exists() and runtime.gateway is None
    with pytest.raises(AppError):runtime.read_value(b'cache:a')
    runtime.connect();assert runtime.gateway is not None

@pytest.mark.parametrize('key,kind', [(b'h','hash'),(b'l','list'),(b's','set'),(b'z','zset'),(b'x','stream'),(b'a','string')])
def test_all_six_value_types_are_read_on_demand(runtime,key,kind):
    result=runtime.read_value(key)
    assert result['kind']==kind and result['rows']
    assert result['total'] is not None


def test_console_real_results_confirmation_and_policy_guards(runtime,services,wire,audit):
    _,console,_,_=services
    result=runtime.execute_confirmed(console,'GET cache:a')
    assert result.ok and result.rows==(('value',),)
    with pytest.raises(AppError):runtime.execute_confirmed(console,'SET fixture v')
    assert not any(p[0]==b'SET' for p in wire.commands)
    result=runtime.execute_confirmed(console,'SET fixture v',confirmed=True)
    assert result.ok and [b'SET',b'fixture',b'v'] in wire.commands
    before=len(wire.commands)
    for raw in ['KEYS *','FLUSHALL','SHUTDOWN','SELECT 3','AUTH secret','HELLO 3']:
        with pytest.raises(AppError):runtime.execute_confirmed(console,raw,confirmed=True,typed=runtime.profile.name)
    assert len(wire.commands)==before
    assert any(row['event']=='command_result' and row['result']=='ok' for row in audit.query())


def test_broken_audit_and_production_readonly_never_send_write(runtime,services,wire,audit):
    _,console,_,_=services
    runtime.profile=replace(runtime.profile,env='prod',protection='readonly')
    with pytest.raises(AppError):runtime.execute_confirmed(console,'SET fixture v',confirmed=True)
    runtime.profile=replace(runtime.profile,env='dev',protection='confirm')
    audit.mark_unwritable()
    with pytest.raises(AppError):runtime.execute_confirmed(console,'SET fixture v',confirmed=True)
    assert not any(p[0]==b'SET' for p in wire.commands)


def test_large_key_index_pages_without_losing_raw_identity(runtime):
    runtime.reset_scan()
    runtime._db.executemany('INSERT OR IGNORE INTO keys(raw) VALUES (?)',[(('key:'+str(i)).encode(),) for i in range(2100)])
    runtime._db.commit()
    assert len(runtime.key_page(0)['keys'])==1000
    assert len(runtime.key_page(2)['keys'])==100
    assert runtime.key_page(1)['total']==2100


def pump_until(predicate, timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        QApplication.processEvents()
        if predicate():return
        time.sleep(.005)
    raise AssertionError('background UI operation did not complete')

@pytest.fixture
def qapp():
    app=QApplication.instance() or QApplication(['workspace-tests'])
    apply_theme(app,'light')
    return app


def test_connection_workspace_binding_tree_and_details(qapp,services,wire):
    connections,console,session,profile=services
    second=replace(profile,id='c2',name='second-connection')
    connections.list_profiles=lambda *a,**kw:[profile,second]
    page=ConnectionsPage(connections,console=console)
    page.set_session(session)
    page.resize(1024,700);page.show();QApplication.processEvents()
    assert page.detail.isHidden()
    assert page.table.columnWidth(0)>=160 and page.table.columnWidth(2)>=170
    assert page.table.item(0,0).toolTip()==profile.name
    page.details_btn.click();QApplication.processEvents();assert not page.detail.isHidden()
    page.details_btn.click()
    first=page.open_workspace('console')
    pump_until(lambda:first.state=='connected')
    page.table.selectRow(1)
    second_ui=page.open_workspace('browser')
    pump_until(lambda:second_ui.state=='connected')
    assert first.profile.id=='c1' and first.io.runtime.profile.id=='c1'
    assert second_ui.profile.id=='c2'
    first.console.input.setPlainText('GET cache:a')
    first.console.execute()
    pump_until(lambda:not first.console.busy)
    assert 'value' in first.console.output.toPlainText()
    browser=second_ui.browser
    browser.start_scan()
    pump_until(lambda:not browser.busy)
    assert browser.tree.topLevelItemCount()>0
    browser.scan_next();pump_until(lambda:not browser.busy)
    assert '游标已结束' in browser.status.text()
    # Prefix leaf and folder must not collide. Raw bytes are stored on leaves only.
    leaves=[]
    def walk(item):
        value=item.data(0,Qt.ItemDataRole.UserRole)
        if isinstance(value,bytes):leaves.append(value)
        for i in range(item.childCount()):walk(item.child(i))
    walk(browser.tree.invisibleRootItem())
    assert {b'cache',b'cache:a',b'cache:a:1',b'\xff:raw',b'h'}.issubset(set(leaves))
    first.io.runtime.retired.set()
    with pytest.raises(AppError):first.io.runtime.execute_confirmed(console,'SET fixture v',True)
    page.invalidate_workspace('c1')
    assert first.closed and first.io.closed
    page.close_workspaces();page.close()
    pump_until(lambda:second_ui.io.runtime.closed)


def test_compact_workspace_hides_manager_instead_of_expanding_window(qapp,services):
    connections,console,session,profile=services
    connections.list_profiles=lambda *a,**kw:[profile]
    page=ConnectionsPage(connections,console=console)
    page.set_session(session)
    page.resize(1024,440);page.show();QApplication.processEvents()
    workspace=page.open_workspace()
    pump_until(lambda:workspace.state=='connected')
    assert page.split.isHidden() and page.manager_header.isHidden()
    assert page.workspace_tabs.isVisible()
    assert page.workspace_tabs.geometry().bottom()<=page.height()
    QApplication.processEvents()
    browser=workspace.browser
    assert browser.next.mapTo(workspace,QPoint(0,0)).y()+browser.next.height()<=workspace.height()
    assert workspace.command_log.toggle.mapTo(workspace,QPoint(0,0)).y()+workspace.command_log.toggle.height()<=workspace.height()+2
    assert workspace.detail.empty.mapTo(workspace,QPoint(0,0)).y()>=0
    page.show_manager();QApplication.processEvents()
    assert page.workspace_tabs.isHidden() and not page.split.isHidden()
    assert workspace.state=='connected'
    assert page.open_workspace('console') is workspace
    page.close_workspaces();page.close()
    pump_until(lambda:workspace.io.runtime.closed)


def test_background_executor_is_serial_and_does_not_block_gui(qapp):
    from redisdesk.app.ui.connection_workspace import AsyncConnection
    class Runtime:
        retired=threading.Event()
        def close(self):pass
    io=AsyncConnection(Runtime())
    main_ident=threading.get_ident();seen=[]
    release=threading.Event()
    io.submit(lambda runtime:(release.wait(1),threading.get_ident())[1],lambda ident:seen.append(ident),lambda exc:pytest.fail(str(exc)))
    from PySide6.QtCore import QTimer
    ticks=[]
    QTimer.singleShot(0,lambda:ticks.append('alive'))
    pump_until(lambda:bool(ticks))
    assert not seen
    release.set();pump_until(lambda:bool(seen))
    assert seen[0]!=main_ident
    io.close();pump_until(lambda:io.cleanup_future.done())


def test_cancel_scan_drops_stale_response_without_loading_values(qapp,services):
    connections,console,session,profile=services
    workspace=ConnectionWorkspace(connections,console,session,profile)
    pump_until(lambda:workspace.state=='connected')
    browser=workspace.browser
    pump_until(lambda:not browser.busy)
    browser.start_scan();browser.cancel_scan()
    pump_until(lambda:not workspace.io.callbacks)
    assert browser.tree.topLevelItemCount()==0
    assert browser.last_value is None
    browser.scan_next();pump_until(lambda:not browser.busy)
    assert browser.tree.topLevelItemCount()>0
    workspace.close_workspace();pump_until(lambda:workspace.io.runtime.closed)


def test_key_paging_and_binary_tree_do_not_fetch_values(qapp,services,wire):
    connections,console,session,profile=services
    workspace=ConnectionWorkspace(connections,console,session,profile)
    pump_until(lambda:workspace.state=='connected')
    browser=workspace.browser
    pump_until(lambda:not browser.busy)
    wire.commands.clear()
    browser.pattern.setText('cache:*')
    browser.scan_all()
    pump_until(lambda:not browser.busy and not browser.auto)
    assert '游标已结束' in browser.status.text()
    assert all(next(parts[i+1] for i in range(len(parts)-1) if parts[i]==b'MATCH')==b'cache:*' for parts in wire.commands if parts[0]==b'SCAN')
    assert not any(parts[0] in {b'GET',b'TYPE',b'TTL'} for parts in wire.commands)
    workspace.close_workspace();pump_until(lambda:workspace.io.runtime.closed)


def test_sensitive_browser_values_stay_masked(runtime):
    result=runtime.read_value(b'token:abc')
    assert result['rows'] and result['rows']!=[('value',)]


def test_closing_main_window_waits_for_cleanup(qapp,services,tmp_store,audit):
    from redisdesk.app.ui.mainwindow import MainWindow
    from redisdesk.app.services.auth_service import AuthService
    from redisdesk.app.services.audit_service import AuditService
    from redisdesk.infra.session import SessionManager
    connections,console,session,profile=services
    connections.list_profiles=lambda *a,**kw:[profile]
    sessions=SessionManager()
    window=MainWindow(auth=AuthService(tmp_store,audit,sessions),connections=connections,
                      console=console,audits=AuditService(audit,connections.grants),sessions=sessions)
    window.bind_session(session);window.show();QApplication.processEvents()
    workspace=window.connections_page.open_workspace()
    pump_until(lambda:workspace.state=='connected')
    release=threading.Event()
    workspace.io.submit(lambda runtime:release.wait(1),lambda result:None,lambda exc:None)
    window.close();QApplication.processEvents()
    assert workspace.io.closed
    assert window.isVisible()  # event deferred until outstanding background work releases resources
    release.set()
    pump_until(lambda:not window.isVisible())
    assert workspace.io.cleanup_future.done() and workspace.io.runtime.closed


def test_value_pagination_preserves_cursor_and_bounds_string_reads(runtime,wire):
    wire.string_value=b'a'*70000
    first=runtime.read_value(b'large')
    assert first['more'] and first['token']==65536
    second=runtime.read_value(b'large',first['token'],'string')
    assert not second['more'] and len(second['rows'][0][0])==4464
    assert not any(parts[0]==b'GET' for parts in wire.commands)
    wire.list_values=[str(i).encode() for i in range(205)]
    first=runtime.read_value(b'l')
    assert len(first['rows'])==100 and first['token']==100 and first['more']
    last=runtime.read_value(b'l',200,'list')
    assert len(last['rows'])==5 and not last['more']
    wire.hash_pages=True
    first=runtime.read_value(b'h')
    assert len(first['rows'])==151 and first['more'] and first['token']==17
    last=runtime.read_value(b'h',first['token'],'hash')
    assert last['rows'][0][0]=='last' and not last['more']
    wire.stream_values=[[(f'1-{i}').encode(),[b'field',b'value']] for i in range(105)]
    first=runtime.read_value(b'x')
    assert len(first['rows'])==100 and first['token']=='1-100'
    last=runtime.read_value(b'x',first['token'],'stream')
    assert len(last['rows'])==5 and not last['more']


def test_workspace_loads_synthetic_stored_credentials_only_in_service(services,wire,monkeypatch):
    connections,console,session,profile=services
    profile=replace(profile,secret_ref='synthetic-secret')
    monkeypatch.setattr(connections.store,'get_secret',lambda sid:object())
    seen=[]
    class Lease:
        def __init__(self,ref):pass
        def __enter__(self):
            seen.append('enter');return memoryview(b'fixture-password')
        def __exit__(self,*args):seen.append('exit')
    monkeypatch.setattr('redisdesk.app.services.connection_service.SecretLease',Lease)
    runtime=WorkspaceSession(connections,session,profile)
    runtime.connect();runtime.close()
    assert seen==['enter','exit']
    assert [b'AUTH',b'fixture-password'] in wire.commands
    wire.commands.clear()
    monkeypatch.setattr(connections.store,'get_secret',lambda sid:None)
    runtime=WorkspaceSession(connections,session,profile)
    with pytest.raises(AppError) as exc:runtime.connect()
    assert exc.value.code=='E02' and not wire.commands
    runtime.close()


def test_confirmation_preview_does_not_leave_duplicate_pending_audit(runtime,services,audit,wire):
    _,console,_,_=services
    prepared=runtime.prepare_console(console,'SET fixture v',preview=True)
    assert prepared.policy.decision=='confirm' and prepared.pending_seq is None
    assert not any(p[0]==b'SET' for p in wire.commands)
    assert not [row for row in audit.query() if row['event']=='command' and row['result']=='pending']
    runtime.execute_confirmed(console,'SET fixture v',confirmed=True)
    pending=[row for row in audit.query() if row['event']=='command' and row['result']=='pending']
    assert len(pending)==1


def test_high_risk_typed_confirmation_is_checked_before_wire(runtime,services,wire):
    _,console,_,_=services
    before=len(wire.commands)
    with pytest.raises(AppError):
        runtime.execute_confirmed(console,'NEWCOMMAND fixture',confirmed=True,typed='another-connection')
    assert len(wire.commands)==before
    with pytest.raises(AppError):
        runtime.execute_confirmed(console,'NEWCOMMAND fixture',confirmed=True,typed=runtime.profile.name)
    assert wire.commands[-1]==[b'NEWCOMMAND',b'fixture']  # simulated NOPERM surfaces as an error


def find_tree_leaf(tree, key):
    found = None
    def walk(item):
        nonlocal found
        if item.data(0, Qt.ItemDataRole.UserRole) == key:
            found = item
            return
        for i in range(item.childCount()):
            walk(item.child(i))
            if found is not None:
                return
    walk(tree.invisibleRootItem())
    return found


def _action_cell(page, row):
    return page.table.cellWidget(row, 7)


def test_row_actions_bind_profile_and_table_fills_width(qapp, services, wire):
    connections, console, session, profile = services
    second = replace(profile, id='c2', name='second-connection')
    connections.list_profiles = lambda *a, **kw: [profile, second]
    page = ConnectionsPage(connections, console=console)
    page.set_session(session)
    page.resize(1280, 800)
    page.show()
    QApplication.processEvents()
    used = sum(page.table.columnWidth(i) for i in range(page.table.columnCount()))
    assert used >= page.table.viewport().width() - 2
    first_actions = _action_cell(page, 0)
    second_actions = _action_cell(page, 1)
    assert first_actions.profile_id == 'c1' and second_actions.profile_id == 'c2'
    assert first_actions.browser_btn.text() == '目录'
    assert first_actions.console_btn.text() == '控制台'
    page.table.selectRow(1)
    first_actions.console_btn.click()
    first = page.current_workspace()
    pump_until(lambda: first is not None and first.state == 'connected')
    assert first.profile.id == 'c1'
    second_actions.browser_btn.click()
    second_ui = page.current_workspace()
    pump_until(lambda: second_ui.state == 'connected')
    assert second_ui.profile.id == 'c2'
    assert page.workspace_tabs.count() == 2
    page.close_workspaces()
    page.close()
    pump_until(lambda: first.io.runtime.closed and second_ui.io.runtime.closed)


def test_connected_empty_state_and_console_are_compact(qapp, services, wire):
    connections, console, session, profile = services
    connections.list_profiles = lambda *a, **kw: [profile]
    page = ConnectionsPage(connections, console=console)
    page.set_session(session)
    page.resize(1024, 700)
    page.show()
    QApplication.processEvents()
    workspace = page.open_workspace('browser')
    pump_until(lambda: workspace.state == 'connected')
    QApplication.processEvents()
    assert '请选择键' in workspace.detail.empty.text()
    assert '未连接' not in workspace.detail.empty.text()
    assert workspace.state_badge.text() == '已连接'
    workspace.show_console()
    QApplication.processEvents()
    run = workspace.console.run
    assert run.objectName() == 'consoleRun'
    assert run.width() <= 96
    assert run.width() < workspace.width() / 4
    workspace.console.input.setPlainText('GET cache:a')
    workspace.console.execute()
    pump_until(lambda: not workspace.console.busy)
    out = workspace.console.output.toPlainText()
    assert '成功' in out and 'value' in out
    workspace.console.input.setPlainText('GET fail')
    workspace.console.execute()
    pump_until(lambda: not workspace.console.busy)
    out = workspace.console.output.toPlainText()
    assert '失败' in out
    log = workspace.command_log.body.toPlainText()
    assert 'GET' in log and 'ERR' in log
    page.close_workspaces()
    page.close()
    pump_until(lambda: workspace.io.runtime.closed)


def test_selecting_leaf_loads_value_without_prefetch(qapp, services, wire):
    connections, console, session, profile = services
    connections.list_profiles = lambda *a, **kw: [profile]
    workspace = ConnectionWorkspace(connections, console, session, profile)
    workspace.resize(800, 600)
    workspace.show()
    pump_until(lambda: workspace.state == 'connected')
    browser = workspace.browser
    pump_until(lambda: not browser.busy and browser.tree.topLevelItemCount() > 0)
    before = [parts for parts in wire.commands if parts[0] in {b'GET', b'TYPE', b'TTL', b'HLEN', b'HSCAN'}]
    target = None
    def walk(item):
        nonlocal target
        if item.data(0, Qt.ItemDataRole.UserRole) == b'h':
            target = item
            return
        for i in range(item.childCount()):
            walk(item.child(i))
            if target is not None:
                return
    walk(browser.tree.invisibleRootItem())
    assert target is not None
    browser.tree.setCurrentItem(target)
    browser._choose(target, 0)
    pump_until(lambda: workspace.detail.last_value is not None)
    assert workspace.detail.last_value['kind'] == 'hash'
    assert workspace.detail.stack.currentIndex() == 1
    assert workspace.detail.hash_view.table.rowCount() >= 1
    assert any(parts[0] == b'TYPE' and parts[1] == b'h' for parts in wire.commands)
    assert '请选择键' not in workspace.detail.empty.text() or workspace.detail.stack.currentIndex() == 1
    page_cmds = [parts[0] for parts in wire.commands if parts[0] in {b'GET', b'TYPE', b'TTL', b'HLEN', b'HSCAN'}]
    assert b'TYPE' in page_cmds
    workspace.close_workspace()
    pump_until(lambda: workspace.io.runtime.closed)


def _assert_not_clipped(child, ancestor, name):
    top = child.mapTo(ancestor, child.rect().topLeft())
    bottom = child.mapTo(ancestor, child.rect().bottomRight())
    assert top.y() >= -2, name
    assert top.x() >= -2, name
    assert bottom.y() <= ancestor.height() + 2, f'{name} clipped y {bottom.y()} > {ancestor.height()}'
    assert bottom.x() <= ancestor.width() + 2, f'{name} clipped x {bottom.x()} > {ancestor.width()}'


def test_offscreen_layout_scenes(qapp, services, wire, tmp_store, audit):
    from redisdesk.app.ui.mainwindow import MainWindow
    from redisdesk.app.services.auth_service import AuthService
    from redisdesk.app.services.audit_service import AuditService
    from redisdesk.infra.session import SessionManager
    from PySide6.QtWidgets import QWidget
    connections, console, session, profile = services
    second = replace(profile, id='c2', name='second-connection')
    connections.list_profiles = lambda *a, **kw: [profile, second]
    sessions = SessionManager()
    window = MainWindow(auth=AuthService(tmp_store, audit, sessions), connections=connections,
                        console=console, audits=AuditService(audit, connections.grants), sessions=sessions)
    window.bind_session(session)
    artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
    artifacts.mkdir(exist_ok=True)

    def capture(name, size):
        window.resize(*size)
        window.show()
        QApplication.processEvents()
        pix = window.grab()
        suffix = os.environ.get('REDISDESK_CAPTURE_SUFFIX', '')
        path = artifacts / f'connection-workspace-{name}{suffix}.png'
        assert pix.save(str(path), 'PNG')
        return path

    window.resize(1280, 800)
    window.show()
    QApplication.processEvents()
    page = window.connections_page
    used = sum(page.table.columnWidth(i) for i in range(page.table.columnCount()))
    assert used >= page.table.viewport().width() - 2
    capture('list-1280x800', (1280, 800))
    capture('list-1024x544', (1024, 544))

    workspace = page.open_workspace('browser', profile)
    pump_until(lambda: workspace.state == 'connected')
    pump_until(lambda: not workspace.browser.busy and workspace.browser.tree.topLevelItemCount() > 0)
    assert '请选择键' in workspace.detail.empty.text()
    assert '未连接' not in workspace.detail.empty.text()
    window._sync_session_status()
    QApplication.processEvents()
    assert '已连接' in window.status_dot.text()
    assert profile.name in window.status_dot.text()

    string_leaf = find_tree_leaf(workspace.browser.tree, b'cache:a')
    assert string_leaf is not None
    workspace.browser.tree.setCurrentItem(string_leaf)
    workspace.browser._choose(string_leaf, 0)
    pump_until(lambda: workspace.detail.last_value is not None and workspace.detail.last_value['kind'] == 'string')
    QApplication.processEvents()
    capture('string-1280x800', (1280, 800))
    capture('string-1024x544', (1024, 544))
    assert workspace.detail.string_view.body.height() >= 80
    assert not workspace.detail.value_more.isVisible()
    assert not workspace.detail.value_prev.isVisible()
    _assert_not_clipped(workspace.browser.tree, workspace, 'tree')
    _assert_not_clipped(workspace.detail.string_view.body, workspace, 'string-body')

    hash_leaf = find_tree_leaf(workspace.browser.tree, b'h')
    assert hash_leaf is not None
    workspace.browser.tree.setCurrentItem(hash_leaf)
    workspace.browser._choose(hash_leaf, 0)
    pump_until(lambda: workspace.detail.last_value is not None and workspace.detail.last_value['kind'] == 'hash')
    QApplication.processEvents()
    capture('hash-1280x800', (1280, 800))
    capture('hash-1024x544', (1024, 544))
    capture('detail-1280x800', (1280, 800))
    capture('detail-1024x544', (1024, 544))
    _assert_not_clipped(workspace.detail.hash_view.table, workspace, 'hash-table')
    _assert_not_clipped(workspace.detail.key_label, workspace, 'key')
    assert not workspace.detail.value_more.isVisible()
    assert not workspace.detail.value_prev.isVisible()

    workspace.show_console()
    workspace.console.input.setPlainText('GET cache:a')
    workspace.console.execute()
    pump_until(lambda: not workspace.console.busy)
    workspace.console.input.setPlainText('GET fail')
    workspace.console.execute()
    pump_until(lambda: not workspace.console.busy)
    QApplication.processEvents()
    out = workspace.console.output.toPlainText()
    assert '成功' in out and '失败' in out and out.strip()
    assert workspace.console.run.width() <= 96
    assert workspace.console.run.width() < window.width() / 4
    _assert_not_clipped(workspace.console.run, workspace, 'run')
    _assert_not_clipped(workspace.console.output, workspace, 'output')
    capture('console-1280x800', (1280, 800))
    capture('console-1024x544', (1024, 544))
    window.close()
    QApplication.processEvents()
    if window.isVisible():
        pump_until(lambda: not window.isVisible())


def test_mainwindow_status_follows_visible_workspace(qapp, services, tmp_store, audit):
    from redisdesk.app.ui.mainwindow import MainWindow
    from redisdesk.app.services.auth_service import AuthService
    from redisdesk.app.services.audit_service import AuditService
    from redisdesk.infra.session import SessionManager
    connections, console, session, profile = services
    connections.list_profiles = lambda *a, **kw: [profile]
    sessions = SessionManager()
    window = MainWindow(auth=AuthService(tmp_store, audit, sessions), connections=connections,
                        console=console, audits=AuditService(audit, connections.grants), sessions=sessions)
    window.bind_session(session)
    window.show()
    QApplication.processEvents()
    window._sync_session_status()
    assert window.env_badge.text() == '未选择'
    assert '未连接' not in window.env_badge.text()
    workspace = window.connections_page.open_workspace('browser')
    pump_until(lambda: workspace.state == 'connected')
    window._sync_session_status()
    QApplication.processEvents()
    assert '已连接' in window.status_dot.text()
    assert profile.name in window.status_dot.text()
    window.close()
    QApplication.processEvents()
    if window.isVisible():
        pump_until(lambda: not window.isVisible())


def test_workspace_split_and_value_area_layout(qapp, services, wire):
    connections, console, session, profile = services
    workspace = ConnectionWorkspace(connections, console, session, profile)
    workspace.resize(1100, 700)
    workspace.show()
    pump_until(lambda: workspace.state == 'connected')
    QApplication.processEvents()
    sizes = workspace.split.sizes()
    total = sum(sizes) or 1
    tree = sizes[0]
    assert 280 <= tree <= 420
    assert 0.30 <= tree / total <= 0.40
    assert not workspace.command_log.is_expanded()
    assert not workspace.command_log.body.isVisible()
    assert workspace.command_log.height() <= 48
    browser = workspace.browser
    pump_until(lambda: not browser.busy and browser.tree.topLevelItemCount() > 0)

    string_leaf = find_tree_leaf(browser.tree, b'cache:a')
    assert string_leaf is not None
    browser.tree.setCurrentItem(string_leaf)
    browser._choose(string_leaf, 0)
    pump_until(lambda: workspace.detail.last_value is not None and workspace.detail.last_value['kind'] == 'string')
    QApplication.processEvents()
    body = workspace.detail.string_view.body
    assert body.isVisible()
    assert body.height() >= 120
    assert body.height() >= workspace.detail.height() * 0.45
    assert not workspace.detail.value_more.isVisible()
    assert not workspace.detail.value_prev.isVisible()
    assert not workspace.detail.hash_view.isVisible()
    assert not workspace.detail.string_more.isVisible()

    hash_leaf = find_tree_leaf(browser.tree, b'h')
    assert hash_leaf is not None
    browser.tree.setCurrentItem(hash_leaf)
    browser._choose(hash_leaf, 0)
    pump_until(lambda: workspace.detail.last_value is not None and workspace.detail.last_value['kind'] == 'hash')
    QApplication.processEvents()
    table = workspace.detail.hash_view.table
    assert table.isVisible()
    assert table.height() >= 120
    assert table.height() >= workspace.detail.height() * 0.35
    assert workspace.detail.hash_view.filter.height() >= 26
    assert not workspace.detail.value_more.isVisible()
    assert not workspace.detail.value_prev.isVisible()
    pager_y = workspace.detail.hash_view.prev.mapTo(workspace.detail.hash_view, QPoint(0, 0)).y()
    table_bottom = table.mapTo(workspace.detail.hash_view, QPoint(0, table.height())).y()
    assert pager_y >= table_bottom - 2
    filter_bottom = workspace.detail.hash_view.filter.mapTo(workspace.detail.hash_view, QPoint(0, workspace.detail.hash_view.filter.height())).y()
    table_top = table.mapTo(workspace.detail.hash_view, QPoint(0, 0)).y()
    assert filter_bottom <= table_top + 2

    workspace.command_log.toggle.click()
    QApplication.processEvents()
    assert workspace.command_log.is_expanded()
    assert 80 <= workspace.command_log.height() <= 180
    workspace.command_log.toggle.click()
    QApplication.processEvents()
    assert not workspace.command_log.is_expanded()

    workspace.close_workspace()
    pump_until(lambda: workspace.io.runtime.closed)


def _open_connected_workspace(qapp, services):
    connections, console, session, profile = services
    workspace = ConnectionWorkspace(connections, console, session, profile)
    workspace.resize(1000, 700)
    workspace.show()
    pump_until(lambda: workspace.state == 'connected')
    pump_until(lambda: not workspace.browser.busy and workspace.browser.tree.topLevelItemCount() > 0)
    return workspace, console, session, profile


def _select_key(workspace, key):
    leaf = find_tree_leaf(workspace.browser.tree, key)
    assert leaf is not None
    workspace.browser.tree.setCurrentItem(leaf)
    workspace.browser._choose(leaf, 0)
    pump_until(lambda: workspace.detail.last_value is not None and workspace.detail.current_key == key)


def _accept_confirm(monkeypatch, *, accepted=True):
    from PySide6.QtWidgets import QDialog
    monkeypatch.setattr(
        'redisdesk.app.ui.connection_workspace.ConfirmRiskDialog.exec',
        lambda self: QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected,
    )


def test_hash_edit_selected_row_value(qapp, services, wire, monkeypatch):
    workspace, console, session, profile = _open_connected_workspace(qapp, services)
    try:
        _select_key(workspace, b'h')
        assert workspace.detail.hash_edit.isVisible()
        assert workspace.detail.hash_view.table.rowCount() >= 1
        assert workspace.detail.hash_view.table.item(0, 0).text() == 'field'
        assert workspace.detail.hash_view.table.item(0, 1).text() == 'value'
        assert not workspace.detail.hash_edit.isEnabled()
        workspace.detail.hash_view.table.selectRow(0)
        QApplication.processEvents()
        assert workspace.detail.hash_edit.isEnabled()
        _accept_confirm(monkeypatch, accepted=True)
        monkeypatch.setattr(
            'redisdesk.app.ui.connection_workspace.QInputDialog.getMultiLineText',
            lambda *args, **kwargs: ('updated-value', True),
        )
        before = len([p for p in wire.commands if p[0] == b'HSET'])
        ttl_before = workspace.detail.ttl_badge.text()
        kind_before = workspace.detail.type_badge.text()
        workspace.detail.hash_edit.click()
        pump_until(lambda: any(p[0] == b'HSET' for p in wire.commands[before:]) and not workspace.console.busy)
        assert [b'HSET', b'h', b'field', b'updated-value'] in wire.commands
        pump_until(lambda: workspace.detail.hash_view.table.item(0, 1) is not None and workspace.detail.hash_view.table.item(0, 1).text() == 'updated-value')
        assert workspace.detail.last_value['kind'] == 'hash'
        assert workspace.detail.ttl_badge.text() == ttl_before
        assert workspace.detail.type_badge.text() == kind_before
        assert workspace.tabs.currentWidget() is workspace.detail
        table = workspace.detail.hash_view.table
        selected = table.currentRow()
        assert selected >= 0
        assert table.item(selected, 0).text() == 'field'
        assert table.item(selected, 1).text() == 'updated-value'
    finally:
        workspace.close_workspace()
        pump_until(lambda: workspace.io.runtime.closed)


def test_hash_edit_requires_selection_and_hash_type(qapp, services, wire, monkeypatch):
    workspace, console, session, profile = _open_connected_workspace(qapp, services)
    try:
        _select_key(workspace, b'h')
        prompted = []
        monkeypatch.setattr(
            'redisdesk.app.ui.connection_workspace.QInputDialog.getMultiLineText',
            lambda *args, **kwargs: prompted.append('opened') or ('nope', True),
        )
        _accept_confirm(monkeypatch, accepted=True)
        workspace.detail.hash_view.table.clearSelection()
        workspace.detail.hash_view.table.setCurrentCell(-1, -1)
        QApplication.processEvents()
        assert not workspace.detail.hash_edit.isEnabled()
        workspace.detail.edit_hash_field()
        QApplication.processEvents()
        assert prompted == []
        assert not any(p[0] == b'HSET' for p in wire.commands)

        _select_key(workspace, b'cache:a')
        pump_until(lambda: workspace.detail.last_value is not None and workspace.detail.last_value['kind'] == 'string')
        assert not workspace.detail.hash_edit.isVisible()
        assert not workspace.detail.hash_add.isVisible()
        workspace.detail.edit_hash_field()
        QApplication.processEvents()
        assert prompted == []
        assert not any(p[0] == b'HSET' for p in wire.commands)
    finally:
        workspace.close_workspace()
        pump_until(lambda: workspace.io.runtime.closed)


def test_hash_edit_production_readonly_and_cancelled_confirm_do_not_write(qapp, services, wire, monkeypatch):
    connections, console, session, profile = services
    readonly = replace(profile, env='prod', protection='readonly')
    workspace = ConnectionWorkspace(connections, console, session, readonly)
    workspace.resize(1000, 700)
    workspace.show()
    try:
        pump_until(lambda: workspace.state == 'connected')
        pump_until(lambda: not workspace.browser.busy and workspace.browser.tree.topLevelItemCount() > 0)
        _select_key(workspace, b'h')
        workspace.detail.hash_view.table.selectRow(0)
        QApplication.processEvents()
        _accept_confirm(monkeypatch, accepted=True)
        monkeypatch.setattr(
            'redisdesk.app.ui.connection_workspace.QInputDialog.getMultiLineText',
            lambda *args, **kwargs: ('should-not-write', True),
        )
        workspace.detail.hash_edit.click()
        pump_until(lambda: not workspace.console.busy)
        assert not any(p[0] == b'HSET' for p in wire.commands)
        assert workspace.detail.hash_view.table.item(0, 1).text() == 'value'
    finally:
        workspace.close_workspace()
        pump_until(lambda: workspace.io.runtime.closed)

    workspace = ConnectionWorkspace(connections, console, session, profile)
    workspace.resize(1000, 700)
    workspace.show()
    try:
        pump_until(lambda: workspace.state == 'connected')
        pump_until(lambda: not workspace.browser.busy and workspace.browser.tree.topLevelItemCount() > 0)
        _select_key(workspace, b'h')
        workspace.detail.hash_view.table.selectRow(0)
        QApplication.processEvents()
        _accept_confirm(monkeypatch, accepted=False)
        monkeypatch.setattr(
            'redisdesk.app.ui.connection_workspace.QInputDialog.getMultiLineText',
            lambda *args, **kwargs: ('cancelled-value', True),
        )
        workspace.detail.hash_edit.click()
        pump_until(lambda: not workspace.console.busy)
        assert not any(p[0] == b'HSET' for p in wire.commands)
        assert '已取消' in workspace.console.output.toPlainText()
        assert workspace.detail.hash_view.table.item(0, 1).text() == 'value'
    finally:
        workspace.close_workspace()
        pump_until(lambda: workspace.io.runtime.closed)


def test_hash_edit_viewer_authz_does_not_write(qapp, services, wire, monkeypatch):
    connections, console, session, profile = services
    connections.grants._grants.add(('c1', 'viewer'))
    viewer = make_session('viewer')
    workspace = ConnectionWorkspace(connections, console, viewer, profile)
    workspace.resize(1000, 700)
    workspace.show()
    try:
        pump_until(lambda: workspace.state == 'connected')
        pump_until(lambda: not workspace.browser.busy and workspace.browser.tree.topLevelItemCount() > 0)
        _select_key(workspace, b'h')
        workspace.detail.hash_view.table.selectRow(0)
        QApplication.processEvents()
        _accept_confirm(monkeypatch, accepted=True)
        monkeypatch.setattr(
            'redisdesk.app.ui.connection_workspace.QInputDialog.getMultiLineText',
            lambda *args, **kwargs: ('viewer-denied', True),
        )
        workspace.detail.hash_edit.click()
        pump_until(lambda: not workspace.console.busy)
        assert not any(p[0] == b'HSET' for p in wire.commands)
        assert workspace.detail.hash_view.table.item(0, 1).text() == 'value'
    finally:
        workspace.close_workspace()
        pump_until(lambda: workspace.io.runtime.closed)


def test_hash_edit_row_action_is_discoverable(qapp, services, wire, monkeypatch):
    workspace, console, session, profile = _open_connected_workspace(qapp, services)
    try:
        _select_key(workspace, b'h')
        table = workspace.detail.hash_view.table
        assert table.horizontalHeaderItem(2).text() == '操作'
        host = table.cellWidget(0, 2)
        assert host is not None
        button = host.findChild(type(workspace.detail.hash_edit))
        assert button is not None and button.text() == '编辑'
        _accept_confirm(monkeypatch, accepted=True)
        monkeypatch.setattr(
            'redisdesk.app.ui.connection_workspace.QInputDialog.getMultiLineText',
            lambda *args, **kwargs: ('from-row-action', True),
        )
        button.click()
        pump_until(lambda: any(p == [b'HSET', b'h', b'field', b'from-row-action'] for p in wire.commands) and not workspace.console.busy)
        pump_until(lambda: workspace.detail.hash_view.table.item(0, 1).text() == 'from-row-action')
    finally:
        workspace.close_workspace()
        pump_until(lambda: workspace.io.runtime.closed)
