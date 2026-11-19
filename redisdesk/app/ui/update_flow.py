"""In-app update check, confirmation, download, and installer launch."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QWidget,
)

from redisdesk import __version__
from redisdesk.app.ui.dialog_kit import DialogSection, ScrollableDialog
from redisdesk.app.ui.jobs import FunctionWorker
from redisdesk.app.ui.theme import UiSettings
from redisdesk.core.errors import AppError
from redisdesk.infra.update import (
    STARTUP_DELAY_MS,
    UpdateOffer,
    check_for_update,
    download_and_verify,
    launch_installer,
    record_last_check,
    record_skip,
    record_snooze,
    should_auto_check,
    should_prompt,
)


class UpdateAvailableDialog(ScrollableDialog):
    CHOICE_INSTALL = "install"
    CHOICE_SNOOZE = "snooze"
    CHOICE_SKIP = "skip"

    def __init__(self, offer: UpdateOffer, parent: QWidget | None = None) -> None:
        super().__init__(
            "发现新版本",
            parent,
            width=520,
            ok_text="立即下载并安装",
            cancel_text="稍后提醒",
            default_ok=False,
        )
        self.offer = offer
        self.choice = self.CHOICE_SNOOZE
        section = DialogSection("更新说明", "安装前请先确认版本与校验信息。下载与安装都需要你再次确认，应用不会自行替换自己。")
        current = QLabel(f"当前版本：{offer.current_version}")
        current.setObjectName("hint")
        latest = QLabel(f"新版本：{offer.version}")
        digest = offer.asset.sha256 or "（未提供，将拒绝安装）"
        size = f"{offer.asset.size} 字节" if offer.asset.size is not None else "未知"
        meta = QLabel(
            f"安装包：{offer.asset.name}\n大小：{size}\nSHA-256：{digest}\n"
            "这是未代码签名的内部预览包，Windows 可能提示未知发布者。本应用不会把它当作已签名。"
        )
        meta.setWordWrap(True)
        meta.setTextInteractionFlags(meta.textInteractionFlags() | meta.textInteractionFlags().TextSelectableByMouse)
        notes = QLabel(offer.notes.strip() or "发布说明见 GitHub Release。")
        notes.setWordWrap(True)
        notes.setTextInteractionFlags(notes.textInteractionFlags() | notes.textInteractionFlags().TextSelectableByMouse)
        section.body.addWidget(current)
        section.body.addWidget(latest)
        section.body.addWidget(meta)
        section.body.addWidget(notes)
        self.body.addWidget(section)
        skip = QPushButton("跳过此版本")
        skip.setObjectName("tool")
        skip.setAutoDefault(False)
        skip.clicked.connect(self._skip)
        self.add_footer_left(skip)
        self.skip_button = skip

    def accept(self) -> None:  # type: ignore[override]
        self.choice = self.CHOICE_INSTALL
        super().accept()

    def reject(self) -> None:  # type: ignore[override]
        if self.choice != self.CHOICE_SKIP:
            self.choice = self.CHOICE_SNOOZE
        super().reject()

    def _skip(self) -> None:
        self.choice = self.CHOICE_SKIP
        self.reject()


class UpdateController(QObject):
    def __init__(self, parent: QWidget, settings: UiSettings | None = None) -> None:
        super().__init__(parent)
        self._host = parent
        self.settings = settings or UiSettings()
        self._worker: FunctionWorker | None = None
        self._busy_message = "正在检查更新…"
        self._on_status = None
        self._automatic = False

    def set_status_handler(self, handler) -> None:  # noqa: ANN001
        self._on_status = handler

    def _emit_status(self, text: str) -> None:
        if self._on_status is not None:
            self._on_status(text)

    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def schedule_auto_check(self, delay_ms: int = STARTUP_DELAY_MS) -> None:
        QTimer.singleShot(max(0, delay_ms), self.maybe_auto_check)

    def maybe_auto_check(self) -> None:
        if not should_auto_check(self.settings):
            return
        self.check(automatic=True)

    def check(self, *, automatic: bool = False) -> None:
        if self.busy():
            return
        self._automatic = automatic
        self._emit_status(self._busy_message)

        def work():
            offer = check_for_update(current_version=__version__)
            return offer

        worker = FunctionWorker(work, parent=self)
        self._worker = worker
        worker.signals.finished.connect(self._on_check_ok)
        worker.signals.failed.connect(self._on_check_fail)
        worker.start()

    def _on_check_ok(self, offer) -> None:  # noqa: ANN001
        self._worker = None
        record_last_check(self.settings)
        if offer is None:
            if not self._automatic:
                self._emit_status(f"当前已是最新版本 {__version__}。")
            else:
                self._emit_status("已检查更新，当前已是最新版本。")
            return
        if self._automatic and not should_prompt(offer, self.settings):
            self._emit_status(f"发现 {offer.version}，已按你的选择暂不提醒。")
            return
        self._emit_status(f"发现新版本 {offer.version}。")
        self._prompt(offer)

    def _on_check_fail(self, exc) -> None:  # noqa: ANN001
        self._worker = None
        message = exc.message if isinstance(exc, AppError) else str(exc)
        if self._automatic:
            self._emit_status("自动检查更新未成功，可稍后在设置中手动检查。")
            return
        self._emit_status(message or "检查更新失败。")

    def _prompt(self, offer: UpdateOffer) -> None:
        dialog = UpdateAvailableDialog(offer, self._host)
        dialog.show()
        QApplication.processEvents()
        dialog.fit_to_work_area()
        result = dialog.exec()
        if dialog.choice == UpdateAvailableDialog.CHOICE_SKIP:
            record_skip(self.settings, offer.version)
            self._emit_status(f"已跳过版本 {offer.version}。")
            return
        if dialog.choice != UpdateAvailableDialog.CHOICE_INSTALL or result != dialog.DialogCode.Accepted:
            record_snooze(self.settings)
            self._emit_status("已稍后提醒。可随时在设置中再次检查。")
            return
        self._download(offer)

    def _download(self, offer: UpdateOffer) -> None:
        progress = QProgressDialog(
            "正在下载并校验安装包…\n这是未签名的内部预览包，不会被当作已签名。",
            None,
            0,
            0,
            self._host,
        )
        progress.setWindowTitle("下载更新")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        def work():
            return download_and_verify(offer)

        worker = FunctionWorker(work, parent=self)
        self._worker = worker

        def ok(path) -> None:  # noqa: ANN001
            self._worker = None
            progress.close()
            self._confirm_install(Path(path), offer)

        def fail(exc) -> None:  # noqa: ANN001
            self._worker = None
            progress.close()
            message = exc.message if isinstance(exc, AppError) else str(exc)
            self._emit_status(message or "下载或校验失败。")
            QMessageBox.warning(self._host, "更新失败", message or type(exc).__name__)

        worker.signals.finished.connect(ok)
        worker.signals.failed.connect(fail)
        worker.start()

    def _confirm_install(self, path: Path, offer: UpdateOffer) -> None:
        text = (
            f"安装包 {offer.asset.name} 已通过 SHA-256 校验，版本 {offer.version}。\n\n"
            "这仍是未代码签名的内部预览，Windows 可能提示未知发布者。请不要绕过安全提示或关闭杀毒软件。\n\n"
            "安装程序不会强制结束 RedisDesk，也不会附带连接、密码或用户数据。"
            "要替换程序文件，需要先退出本应用。\n\n"
            "要现在启动安装向导并退出 RedisDesk 吗？"
        )
        box = QMessageBox(self._host)
        box.setWindowTitle("确认安装")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(text)
        install = box.addButton("启动安装并退出", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(install)
        box.exec()
        if box.clickedButton() is not install:
            self._emit_status("已取消安装。校验过的安装包仍保留在本机更新目录。")
            return
        try:
            launch_installer(path)
        except AppError as exc:
            self._emit_status(exc.message)
            QMessageBox.warning(self._host, "无法启动安装程序", exc.message)
            return
        self._emit_status("安装向导已启动，正在退出 RedisDesk。")
        app = QApplication.instance()
        window = self._host.window() if self._host is not None else None
        if window is not None:
            window.close()
        elif app is not None:
            app.quit()
