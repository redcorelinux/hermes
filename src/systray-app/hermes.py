#!/usr/bin/env python3

import sys
import os
import time
import signal
from collections import OrderedDict

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

IGNORE_FILE = os.path.expanduser("~/.hermes_upgrade_ignore")
AUTOSTART_DIR = os.path.expanduser("~/.config/autostart")
AUTOSTART_FILE = os.path.join(AUTOSTART_DIR, "hermes.desktop")

SERVICE_NAME = 'org.hermesd.MessageService'
OBJECT_PATH = '/org/hermesd/MessageObject'
INTERFACE = 'org.hermesd.MessageInterface'


class HermesDBusHandler(QtCore.QObject):
    messageReceived = QtCore.pyqtSignal(str)
    heartbeatReceived = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.session_bus = QDBusConnection.systemBus()
        self.session_bus.connect(
            SERVICE_NAME,
            OBJECT_PATH,
            INTERFACE,
            "MessageSent",
            self.handle_message
        )
        self.session_bus.connect(
            SERVICE_NAME,
            OBJECT_PATH,
            INTERFACE,
            "Heartbeat",
            self.handle_heartbeat
        )

    @QtCore.pyqtSlot(str)
    def handle_message(self, message):
        self.messageReceived.emit(message)

    @QtCore.pyqtSlot()
    def handle_heartbeat(self):
        self.heartbeatReceived.emit()

    def get_status(self):
        iface = QDBusInterface(
            SERVICE_NAME,
            OBJECT_PATH,
            INTERFACE,
            self.session_bus
        )
        reply = iface.call("GetStatus")
        if reply.type() == QDBusMessage.MessageType.ReplyMessage:
            return reply.arguments()[0]
        return None


class SysTrayGui(QtCore.QObject):
    def __init__(self):
        super().__init__()

        if not QtWidgets.QApplication.instance():
            self.app = QtWidgets.QApplication(sys.argv)
        else:
            self.app = QtWidgets.QApplication.instance()

        self.ignore_durations = OrderedDict([
            ("Ignore notifications for 1 day", 24*3600),
            ("Ignore notifications for 7 days", 7*24*3600),
            ("Ignore notifications for 15 days", 15*24*3600),
            ("Ignore notifications for 30 days", 30*24*3600),
            ("Receive notifications", 0)
        ])

        self.tray = QtWidgets.QSystemTrayIcon(
            QtGui.QIcon.fromTheme("utilities-system-monitor"))
        self.tray.setToolTip("Hermes: System Upgrade Notifications")
        self.tray.setVisible(True)

        self.menu = QtWidgets.QMenu()
        for label in self.ignore_durations:
            action = QtGui.QAction(label, self.menu)
            action.triggered.connect(
                lambda checked, l=label: self.set_ignore(l))
            self.menu.addAction(action)

        self.menu.addSeparator()

        self.add_autostart_action = QtGui.QAction(
            "Enable Autostart", self.menu)
        self.add_autostart_action.triggered.connect(self.add_to_autostart)
        self.menu.addAction(self.add_autostart_action)

        self.remove_autostart_action = QtGui.QAction(
            "Disable Autostart", self.menu)
        self.remove_autostart_action.triggered.connect(
            self.remove_from_autostart)
        self.menu.addAction(self.remove_autostart_action)

        self.menu.addSeparator()

        quit_action = QtGui.QAction("Quit", self.menu)
        quit_action.triggered.connect(self.quit_app)
        self.menu.addAction(quit_action)

        self.tray.setContextMenu(self.menu)

        self.heartbeat_timer = QtCore.QTimer()
        self.heartbeat_timer.setInterval(1 * 3600 * 1000)  # 1 hour
        self.heartbeat_timer.timeout.connect(self.missed_heartbeat)
        self.heartbeat_timer.start()

        self.signal_timer = QtCore.QTimer()
        self.signal_timer.timeout.connect(lambda: None)
        self.signal_timer.start(100)  # every 100 ms

    def set_ignore(self, label):
        duration = self.ignore_durations[label]
        if duration == 0:
            if os.path.exists(IGNORE_FILE):
                os.remove(IGNORE_FILE)
            self.tray.showMessage("Ignore Cleared", "Receive upgrade notifications.",
                                  QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        else:
            expiry = int(time.time()) + duration
            try:
                with open(IGNORE_FILE, "w") as f:
                    f.write(str(expiry))
                text = label[25:] if len(label) > 25 else label
                self.tray.showMessage(
                    "Ignore Set", f"Ignoring upgrade notifications for {text}.", QtWidgets.QSystemTrayIcon.MessageIcon.Information)
            except Exception:
                pass

    def is_ignored(self):
        if not os.path.exists(IGNORE_FILE):
            return False
        try:
            expiry = int(open(IGNORE_FILE).read().strip())
            return time.time() < expiry
        except Exception:
            return False

    def handle_message(self, message):
        self.heartbeat_timer.start()

        if message == "no_internet":
            self.tray.showMessage("No Internet Connection", "Unable to check for system upgrade because no internet connection is available.",
                                  QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        elif message == "blocked_sync":
            self.tray.showMessage("Sync Failure", "Unable to sync the portage tree and overlays to check for system upgrade.",
                                  QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        elif message == "upgrade_check_failed":
            self.tray.showMessage("Check Failure", "Unable to check for system upgrade.",
                                  QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        elif message == "orphan_check_failed":
            self.tray.showMessage("Check Failure", "Unable to check for orphaned packages.",
                                  QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        elif message == "blocked_upgrade":
            if not self.is_ignored():
                self.tray.showMessage("Blocked Upgrade", "System upgrade is available but blocked due to portage configuration issues.",
                                      QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        elif message == "orphans_detected":
            if not self.is_ignored():
                self.tray.showMessage("Orphans Detected", "The system is up to date, but orphaned packages have been detected.",
                                      QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        elif message == "upgrade_detected":
            if not self.is_ignored():
                self.tray.showMessage("System Upgrade", "System upgrade is available to improve security, stability and performance.",
                                      QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        elif message == "up_to_date":
            if not self.is_ignored():
                self.tray.showMessage("Up to date", "The system is up to date, will check again in 6 hours.",
                                      QtWidgets.QSystemTrayIcon.MessageIcon.Information)

    def handle_heartbeat(self):
        self.heartbeat_timer.start()

    def missed_heartbeat(self):
        self.tray.showMessage("Heartbeat Missed", "No heartbeat message received in over 1 hour. The daemon may be offline.",
                              QtWidgets.QSystemTrayIcon.MessageIcon.Warning)

    def add_to_autostart(self):
        try:
            if not os.path.exists(AUTOSTART_DIR):
                os.makedirs(AUTOSTART_DIR)
            exec_path = sys.argv[0]
            desktop_entry = f"""[Desktop Entry]
Type=Application
Exec={exec_path}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Name=Hermes
Comment=Tray notifications for system upgrades
"""
            with open(AUTOSTART_FILE, 'w') as f:
                f.write(desktop_entry)
            self.tray.showMessage(
                "Autostart Enabled", "", QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        except Exception:
            self.tray.showMessage(
                "Autostart Enable Failed", "", QtWidgets.QSystemTrayIcon.MessageIcon.Critical)

    def remove_from_autostart(self):
        try:
            if os.path.exists(AUTOSTART_FILE):
                os.remove(AUTOSTART_FILE)
                self.tray.showMessage(
                    "Autostart Disabled", "", QtWidgets.QSystemTrayIcon.MessageIcon.Information)
            else:
                self.tray.showMessage(
                    "Autostart Not Found", "", QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        except Exception:
            self.tray.showMessage(
                "Autostart Disable Failed", "", QtWidgets.QSystemTrayIcon.MessageIcon.Critical)

    def quit_app(self):
        self.tray.hide()
        self.app.quit()

    def run(self):
        self.app.exec()


def signal_handler(sig, frame):
    sys.exit(0)


if __name__ == "__main__":
    if not QtWidgets.QApplication.instance():
        app = QtWidgets.QApplication(sys.argv)
    else:
        app = QtWidgets.QApplication.instance()

    dbus_handler = HermesDBusHandler()
    gui = SysTrayGui()
    dbus_handler.messageReceived.connect(gui.handle_message)
    dbus_handler.heartbeatReceived.connect(gui.handle_heartbeat)

    # Query current status after 15 minutes delay
    def delayed_status_query():
        status = dbus_handler.get_status()
        if status:
            gui.handle_message(status)

    QtCore.QTimer.singleShot(15 * 60 * 1000, delayed_status_query)

    signal.signal(signal.SIGINT, lambda sig, frame: gui.quit_app())
    signal.signal(signal.SIGTERM, lambda sig, frame: gui.quit_app())

    gui.run()
