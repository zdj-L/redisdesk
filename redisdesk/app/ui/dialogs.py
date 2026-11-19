"""连接编辑、风险确认、口令/理由输入。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from redisdesk.app.services.connection_service import ConnectionService
from redisdesk.app.ui.dialog_kit import DialogSection, ScrollableDialog, StyledComboBox, configure_spinbox
from redisdesk.app.ui.jobs import FunctionWorker
from redisdesk.core.errors import AppError
from redisdesk.core.model import SessionContext
from redisdesk.app.ui.i18n import ENV_LABELS, PROTECTION_LABELS
from redisdesk.core.limits import PLAINTEXT_REASON_MIN_CHARS
from redisdesk.core.model import ConnectionProfile, Endpoint, SshConfig, TlsConfig
from redisdesk.infra.store import new_id


def _form() -> QFormLayout:
    form = QFormLayout()
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(8)
    form.setContentsMargins(0, 0, 0, 0)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    return form


def _combo(options: dict[str, str], current: str | None = None) -> StyledComboBox:
    box = StyledComboBox()
    for value, text in options.items():
        box.addItem(text, value)
    if current:
        index = box.findData(current)
        if index >= 0:
            box.setCurrentIndex(index)
    return box


class PasswordDialog(ScrollableDialog):
    def __init__(self, title: str, prompt: str, parent=None) -> None:
        super().__init__(title, parent, width=440)
        self.body.addWidget(QLabel(prompt))
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.body.addWidget(self.edit)
        self.edit.setFocus()

    def value(self) -> str:
        return self.edit.text()


class ReasonDialog(ScrollableDialog):
    def __init__(self, title: str, prompt: str, parent=None, min_chars: int = PLAINTEXT_REASON_MIN_CHARS) -> None:
        super().__init__(title, parent, width=480)
        self.min_chars = min_chars
        self.body.addWidget(QLabel(prompt))
        self.edit = QTextEdit()
        self.edit.setFixedHeight(100)
        self.body.addWidget(self.edit)
        self.hint = QLabel(f"至少 {min_chars} 个字符")
        self.hint.setObjectName("hint")
        self.body.addWidget(self.hint)
        self.ok_button.setEnabled(False)
        self.edit.textChanged.connect(self._sync)

    def _sync(self) -> None:
        self.ok_button.setEnabled(len(self.edit.toPlainText().strip()) >= self.min_chars)

    def value(self) -> str:
        return self.edit.toPlainText().strip()


class ConfirmRiskDialog(ScrollableDialog):
    def __init__(
        self,
        *,
        title: str,
        command: str,
        consequence: str,
        typed_text: str = "",
        require_typed: bool = False,
        parent=None,
    ) -> None:
        super().__init__(
            title,
            parent,
            width=520,
            ok_text="确认执行",
            ok_role="danger",
            default_ok=False,
        )
        self._expected = typed_text
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        heading.setWordWrap(True)
        self.body.addWidget(heading)
        cmd = QLabel(command)
        cmd.setObjectName("mono")
        cmd.setTextInteractionFlags(cmd.textInteractionFlags() | cmd.textInteractionFlags().TextSelectableByMouse)
        cmd.setWordWrap(True)
        self.body.addWidget(cmd)
        cons = QLabel(consequence)
        cons.setObjectName("inlineWarn")
        cons.setWordWrap(True)
        self.body.addWidget(cons)
        self.typed = QLineEdit()
        if require_typed:
            tip = QLabel(f"请输入连接名称「{typed_text}」以确认")
            tip.setObjectName("hint")
            self.body.addWidget(tip)
            self.body.addWidget(self.typed)
            self.ok_button.setEnabled(False)
            self.typed.textChanged.connect(lambda v: self.ok_button.setEnabled(v == self._expected))
        else:
            self.typed.hide()


class ConnectionEditorDialog(ScrollableDialog):
    def __init__(
        self,
        profile: ConnectionProfile | None = None,
        parent=None,
        *,
        session: SessionContext | None = None,
        service: ConnectionService | None = None,
    ) -> None:
        super().__init__("编辑连接" if profile else "新建连接", parent, width=560)
        self._id = profile.id if profile else new_id("c")
        self._session = session
        self._service = service
        self._test_worker: FunctionWorker | None = None
        self._validation_attempted = False

        basic = DialogSection("基本信息")
        form = _form()
        self.name = QLineEdit(profile.name if profile else "")
        self.name.setPlaceholderText("例如 orders-dev")
        self.host = QLineEdit(profile.host if profile else "127.0.0.1")
        self.port = configure_spinbox(QSpinBox())
        self.port.setRange(1, 65535)
        self.port.setValue(profile.port if profile else 6379)
        self.db = configure_spinbox(QSpinBox())
        self.db.setRange(0, 15)
        self.db.setValue(profile.db if profile else 0)
        self.env = _combo(ENV_LABELS, profile.env if profile else "dev")
        form.addRow("名称", self.name)
        form.addRow("主机", self.host)
        form.addRow("端口", self.port)
        form.addRow("数据库", self.db)
        form.addRow("环境", self.env)
        basic.body.addLayout(form)
        self.body.addWidget(basic)

        auth = DialogSection("认证", "密码保存在本机。编辑时留空表示不修改已保存的密码。")
        auth_form = _form()
        self.username = QLineEdit(profile.username or "" if profile else "")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("不修改请留空" if profile else "可选")
        auth_form.addRow("用户名", self.username)
        auth_form.addRow("密码", self.password)
        auth.body.addLayout(auth_form)
        self.body.addWidget(auth)

        ssh = DialogSection("SSH 跳板", "从本机先 SSH 到跳板，再由跳板连接 Redis。跳板密码与私钥口令保存在本机，编辑时留空表示不修改。")
        self.use_ssh = QCheckBox("使用 SSH 跳板")
        existing_ssh = profile.ssh if profile and profile.transport == "ssh" and profile.ssh else None
        self.use_ssh.setChecked(existing_ssh is not None)
        self._ssh_password_ref = existing_ssh.password_ref if existing_ssh else None
        self._ssh_passphrase_ref = existing_ssh.passphrase_ref if existing_ssh else None
        self._ssh_password_auth_flagged = bool(existing_ssh.password_auth_flagged) if existing_ssh else False
        self._ssh_known_hosts_policy = existing_ssh.known_hosts_policy if existing_ssh else "tofu"
        self.ssh_block = QWidget()
        ssh_form = _form()
        self.ssh_host = QLineEdit(existing_ssh.host if existing_ssh else "")
        self.ssh_port = configure_spinbox(QSpinBox())
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.setValue(existing_ssh.port if existing_ssh else 22)
        self.ssh_username = QLineEdit(existing_ssh.username if existing_ssh else "")
        self.ssh_auth = _combo({"password": "密码", "key": "私钥文件"}, existing_ssh.auth_mode if existing_ssh else "password")
        self.ssh_password = QLineEdit()
        self.ssh_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssh_password.setPlaceholderText("不修改请留空" if existing_ssh and existing_ssh.password_ref else "可选")
        self.ssh_key_path = QLineEdit(existing_ssh.private_key_path or "" if existing_ssh else "")
        self.ssh_key_path.setPlaceholderText("私钥文件路径")
        self.ssh_key_browse = QPushButton("浏览…")
        self.ssh_key_browse.setObjectName("tool")
        self.ssh_key_browse.setAutoDefault(False)
        key_row = QWidget()
        key_layout = _form()
        # keep a compact path + browse row via form later
        key_wrap = QWidget()
        key_bar = QHBoxLayout(key_wrap)
        key_bar.setContentsMargins(0, 0, 0, 0)
        key_bar.setSpacing(8)
        key_bar.addWidget(self.ssh_key_path, 1)
        key_bar.addWidget(self.ssh_key_browse)
        self.ssh_passphrase = QLineEdit()
        self.ssh_passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.ssh_passphrase.setPlaceholderText("不修改请留空" if existing_ssh and existing_ssh.passphrase_ref else "可选")
        ssh_form.addRow("跳板主机", self.ssh_host)
        ssh_form.addRow("跳板端口", self.ssh_port)
        ssh_form.addRow("跳板用户名", self.ssh_username)
        ssh_form.addRow("认证方式", self.ssh_auth)
        self.ssh_password_label = ssh_form.labelForField(self.ssh_password) if False else None
        ssh_form.addRow("跳板密码", self.ssh_password)
        self._ssh_password_row_index = ssh_form.rowCount() - 1
        ssh_form.addRow("私钥文件", key_wrap)
        self._ssh_key_row_index = ssh_form.rowCount() - 1
        ssh_form.addRow("私钥口令", self.ssh_passphrase)
        self._ssh_pass_row_index = ssh_form.rowCount() - 1
        self._ssh_form = ssh_form
        self.ssh_block.setLayout(ssh_form)
        ssh.body.addWidget(self.use_ssh)
        ssh.body.addWidget(self.ssh_block)
        self.body.addWidget(ssh)
        self.use_ssh.toggled.connect(self._sync_ssh_ui)
        self.ssh_auth.currentIndexChanged.connect(self._sync_ssh_ui)
        self.ssh_key_browse.clicked.connect(self._browse_ssh_key)

        security = DialogSection(

            "安全与 TLS",
            "默认启用 TLS 1.2+。「允许明文」是不使用 TLS 加密传输，不是明文保存密码；启用时须填写不少于 10 字的理由。",
        )
        tls_on = profile.tls.enabled if profile else True
        self.tls = QCheckBox("启用 TLS（推荐）")
        self.tls.setChecked(tls_on)
        self.tls.setEnabled(True)
        self.ca_row = QWidget()
        ca_form = _form()
        self.ca_path = QLineEdit(profile.tls.ca_path or "" if profile else "")
        self.ca_path.setPlaceholderText("自签证书时填写 CA 文件路径")
        ca_form.addRow("CA 路径", self.ca_path)
        self.ca_row.setLayout(ca_form)
        self.allow_plain = QCheckBox("允许明文")
        # 关闭 TLS 时预先勾选「允许明文」，避免一打开就是「两项都不勾」的矛盾状态。
        if not tls_on:
            self.allow_plain.setChecked(True)
        else:
            self.allow_plain.setChecked(bool(profile and profile.tls.allow_plaintext))
        self.allow_plain.setEnabled(True)
        self.reason_row = QWidget()
        reason_form = _form()
        self.reason = QLineEdit(profile.tls.plaintext_reason or "" if profile else "")
        self.reason.setPlaceholderText("明文例外理由（不少于 10 字）")
        reason_form.addRow("明文理由", self.reason)
        self.reason_row.setLayout(reason_form)
        security.body.addWidget(self.tls)
        security.body.addWidget(self.ca_row)
        security.body.addWidget(self.allow_plain)
        security.body.addWidget(self.reason_row)
        self.body.addWidget(security)

        policy = DialogSection("保护策略", "生产环境将强制只读。支持直连 TCP，以及可选的 SSH 跳板。暂不支持 Cluster / Sentinel。")
        self.protection = _combo(PROTECTION_LABELS, profile.protection if profile else "confirm")
        pol_form = _form()
        pol_form.addRow("保护档位", self.protection)
        policy.body.addLayout(pol_form)
        self.body.addWidget(policy)

        self.env.currentIndexChanged.connect(self._on_env)
        self.tls.toggled.connect(self._on_tls_toggled)
        self.allow_plain.toggled.connect(self._on_allow_plain_toggled)
        self.reason.textChanged.connect(self._on_reason_changed)
        self._on_env()
        self._sync_tls_ui(user_toggled=False)
        self._sync_ssh_ui()
        self.name.setFocus()

        self.test_button = QPushButton("测试连接")
        self.test_button.setAutoDefault(False)
        self.test_button.clicked.connect(self._test_from_form)
        self.add_footer_left(self.test_button)

    def _on_env(self, *_args) -> None:
        env = self.env.currentData()
        if env == "prod":
            index = self.protection.findData("readonly")
            if index >= 0:
                self.protection.setCurrentIndex(index)
            self.protection.setEnabled(False)
        else:
            self.protection.setEnabled(True)

    def _set_reason_invalid(self, invalid: bool) -> None:
        self.reason.setProperty("invalid", invalid)
        style = self.reason.style()
        if style is not None:
            style.unpolish(self.reason)
            style.polish(self.reason)

    def _plaintext_status_messages(self) -> tuple[str, str]:
        return (
            "关闭 TLS 时请勾选「允许明文」。",
            "请填写不少于 10 字的明文例外理由。",
        )

    def _is_plaintext_validation_status(self) -> bool:
        return self.status_label.text() in self._plaintext_status_messages()

    def _reason_row_should_show(self) -> bool:
        return (not self.tls.isChecked()) and self.allow_plain.isChecked()

    def _refresh_validation_hints(self) -> None:
        # 未尝试测试/保存前，不标红、不弹错误条。
        self.reason_row.setVisible(self._reason_row_should_show())
        if not self._validation_attempted:
            self._set_reason_invalid(False)
            return
        message = self._plaintext_validation_message()
        if message:
            # 只有已勾选「允许明文」且理由不足时，才给理由框标红。
            reason_error = self._reason_row_should_show() and len(self.reason.text().strip()) < PLAINTEXT_REASON_MIN_CHARS
            self._set_reason_invalid(reason_error)
            self.set_status("danger", message)
            return
        self._set_reason_invalid(False)
        if self._is_plaintext_validation_status():
            self.clear_status()

    def _sync_tls_ui(self, *, user_toggled: bool) -> None:
        self.tls.setEnabled(True)
        self.allow_plain.setEnabled(True)
        tls_on = self.tls.isChecked()
        self.ca_row.setVisible(tls_on)
        self.allow_plain.setVisible(not tls_on)
        if tls_on:
            self.reason_row.setVisible(False)
            if self._validation_attempted and self._is_plaintext_validation_status():
                self._validation_attempted = False
                self.clear_status()
            self._set_reason_invalid(False)
            return
        # 关闭 TLS 时自动勾选「允许明文」，再显示空的理由栏，此时还不报错。
        if not self.allow_plain.isChecked():
            self.allow_plain.blockSignals(True)
            self.allow_plain.setChecked(True)
            self.allow_plain.blockSignals(False)
        self.reason_row.setVisible(True)
        if user_toggled:
            self.reason.setFocus()
        self._refresh_validation_hints()

    def _on_tls_toggled(self, checked: bool) -> None:  # noqa: ARG002
        self._sync_tls_ui(user_toggled=True)

    def _on_allow_plain_toggled(self, checked: bool) -> None:
        if self.tls.isChecked():
            self.reason_row.setVisible(False)
            self._set_reason_invalid(False)
            return
        if not checked:
            self.reason_row.setVisible(False)
            self._set_reason_invalid(False)
        else:
            self.reason_row.setVisible(True)
        self._refresh_validation_hints()
        if checked and not self._validation_attempted:
            self.reason.setFocus()

    def _on_reason_changed(self, *_args) -> None:
        self._refresh_validation_hints()

    def _plaintext_validation_message(self) -> str | None:
        if self.tls.isChecked():
            return None
        if not self.allow_plain.isChecked():
            return "关闭 TLS 时请勾选「允许明文」。"
        if len(self.reason.text().strip()) < PLAINTEXT_REASON_MIN_CHARS:
            return "请填写不少于 10 字的明文例外理由。"
        return None


    def _sync_ssh_ui(self, *_args) -> None:
        enabled = self.use_ssh.isChecked()
        self.ssh_block.setVisible(enabled)
        if not enabled:
            return
        mode = self.ssh_auth.currentData() or "password"
        password_mode = mode == "password"
        self._set_ssh_row_visible(self._ssh_password_row_index, password_mode)
        self._set_ssh_row_visible(self._ssh_key_row_index, not password_mode)
        self._set_ssh_row_visible(self._ssh_pass_row_index, not password_mode)

    def _set_ssh_row_visible(self, row: int, visible: bool) -> None:
        for role in (QFormLayout.ItemRole.LabelRole, QFormLayout.ItemRole.FieldRole):
            item = self._ssh_form.itemAt(row, role)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setVisible(visible)

    def _browse_ssh_key(self) -> None:
        path, _filt = QFileDialog.getOpenFileName(self, "选择 SSH 私钥", self.ssh_key_path.text().strip() or "")
        if path:
            self.ssh_key_path.setText(path)

    def ssh_password_value(self) -> str:
        return self.ssh_password.text()

    def ssh_passphrase_value(self) -> str:
        return self.ssh_passphrase.text()

    def _ssh_validation_message(self) -> str | None:
        if not self.use_ssh.isChecked():
            return None
        if not self.ssh_host.text().strip():
            return "请填写 SSH 跳板主机。"
        if not self.ssh_username.text().strip():
            return "请填写 SSH 跳板用户名。"
        mode = self.ssh_auth.currentData() or "password"
        if mode == "password":
            if not self.ssh_password.text() and not self._ssh_password_ref:
                return "请填写 SSH 跳板密码。"
        else:
            if not self.ssh_key_path.text().strip():
                return "请选择 SSH 私钥文件。"
        return None

    def password_value(self) -> str:
        return self.password.text()

    def profile(self) -> ConnectionProfile:
        if self.allow_plain.isChecked() and not self.tls.isChecked():
            tls = TlsConfig(
                enabled=False,
                ca_path=self.ca_path.text().strip() or None,
                allow_plaintext=True,
                plaintext_reason=self.reason.text().strip() or None,
                verify_hostname=True,
            )
        else:
            tls = TlsConfig(
                enabled=self.tls.isChecked(),
                ca_path=self.ca_path.text().strip() or None,
                allow_plaintext=False,
                plaintext_reason=self.reason.text().strip() or None,
                verify_hostname=True,
            )
        env = self.env.currentData() or "dev"
        protection = "readonly" if env == "prod" else (self.protection.currentData() or "confirm")
        ssh = None
        transport = "tcp"
        if self.use_ssh.isChecked():
            transport = "ssh"
            auth_mode = self.ssh_auth.currentData() or "password"
            ssh = SshConfig(
                host=self.ssh_host.text().strip(),
                port=int(self.ssh_port.value()),
                username=self.ssh_username.text().strip(),
                auth_mode=auth_mode,
                private_key_path=self.ssh_key_path.text().strip() or None,
                passphrase_ref=self._ssh_passphrase_ref,
                known_hosts_policy=self._ssh_known_hosts_policy or "tofu",
                password_auth_flagged=self._ssh_password_auth_flagged or auth_mode == "password",
                password_ref=self._ssh_password_ref,
            )
        return ConnectionProfile(
            id=self._id,
            name=self.name.text().strip(),
            topology="direct",
            transport=transport,
            endpoint=Endpoint(self.host.text().strip(), int(self.port.value())),
            db=int(self.db.value()),
            username=self.username.text().strip() or None,
            tls=tls,
            ssh=ssh,
            env=env,
            protection=protection,
        )

    def _show_banner(self, kind: str, text: str) -> None:
        self.set_status(kind, text)

    def accept(self) -> None:
        self._validation_attempted = True
        message = self._plaintext_validation_message()
        if message:
            self._refresh_validation_hints()
            self._focus_plaintext_requirement()
            return
        ssh_message = self._ssh_validation_message()
        if ssh_message:
            self.set_status("danger", ssh_message)
            if self.use_ssh.isChecked():
                self.ssh_host.setFocus()
            return
        self._set_reason_invalid(False)
        if self._is_plaintext_validation_status():
            self.clear_status()
        super().accept()

    def _focus_plaintext_requirement(self) -> None:
        if self.tls.isChecked():
            return
        if self.allow_plain.isChecked():
            self.reason.setFocus()
        else:
            self.allow_plain.setFocus()

    def _test_from_form(self) -> None:
        if self._test_worker is not None and self._test_worker.isRunning():
            return
        self._validation_attempted = True
        message = self._plaintext_validation_message()
        if message:
            self._refresh_validation_hints()
            self._focus_plaintext_requirement()
            return
        ssh_message = self._ssh_validation_message()
        if ssh_message:
            self.set_status("danger", ssh_message)
            return
        if self._session is None or self._service is None:
            self.set_status("warn", "当前无法测试：尚未绑定本机会话。")
            return
        profile = self.profile()
        if not profile.host:
            self.set_status("warn", "请先填写主机地址。")
            return
        self.test_button.setEnabled(False)
        self.test_button.setText("测试中…")
        self.set_status("info", "正在测试连接…")
        password = self.password_value()
        session = self._session
        service = self._service

        def work():
            return service.test_connect(
                session,
                profile,
                password=password or None,
                ssh_password=self.ssh_password_value() or None,
                ssh_passphrase=self.ssh_passphrase_value() or None,
                require_grant=False,
            )

        worker = FunctionWorker(work, parent=self)
        self._test_worker = worker
        worker.signals.finished.connect(self._on_draft_test_ok)
        worker.signals.failed.connect(self._on_draft_test_fail)
        worker.finished.connect(self._on_draft_test_settled)
        worker.start()

    def _on_draft_test_ok(self, result) -> None:  # noqa: ANN001
        extra = ""
        if getattr(result, "first_default_warning", False):
            extra = " 检测到可能使用了默认用户，请确认 ACL 配置。"
        host = self.host.text().strip() or "目标"
        self.set_status("ok", f"已成功连接到 {host}。{extra}".strip())

    def _on_draft_test_fail(self, exc) -> None:  # noqa: ANN001
        message = exc.message if isinstance(exc, AppError) else str(exc)
        self.set_status("danger", message)

    def _on_draft_test_settled(self) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连接")
