#!/usr/bin/env python3
import sys
import os
import time
import subprocess
from collections import OrderedDict
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

IGNORE_FILE = os.path.expanduser("~/.sisyphus_upgrade_ignore")


class SysTrayListener(QtCore.QObject):
    def __init__(self):
        super().__init__()

        if not QtWidgets.QApplication.instance():
            self.app = QtWidgets.QApplication(sys.argv)
        else:
            self.app = QtWidgets.QApplication.instance()

        self.tray = QtWidgets.QSystemTrayIcon(
            QtGui.QIcon.fromTheme("utilities-system-monitor"))
        self.tray.setVisible(True)

        self.ignore_durations = OrderedDict([
            ("Ignore notifications for 1 day", 24*3600),
            ("Ignore notifications for 7 days", 7*24*3600),
            ("Ignore notifications for 15 days", 15*24*3600),
            ("Ignore notifications for 30 days", 30*24*3600),
            ("Receive notifications", 0)
        ])

        self.menu = QtWidgets.QMenu()
        for label in self.ignore_durations:
            action = QtGui.QAction(label, self.menu)
            action.triggered.connect(
                lambda checked, l=label: self.set_ignore(l))
            self.menu.addAction(action)

        self.menu.addSeparator()

        launch_action = QtGui.QAction("Launch Sisyphus GUI", self.menu)
        launch_action.triggered.connect(self.launch_main_app)
        self.menu.addAction(launch_action)

        quit_action = QtGui.QAction("Quit", self.menu)
        quit_action.triggered.connect(self.quit_app)
        self.menu.addAction(quit_action)

        self.tray.setContextMenu(self.menu)

        self.session_bus = QDBusConnection.sessionBus()
        if not self.session_bus.isConnected():
            sys.exit(1)

        self.session_bus.connect(
            "org.hermesd.MessageService",
            "/org/hermesd/MessageObject",
            "org.hermesd.MessageInterface",
            "MessageSent",
            self.on_message_received
        )

        QtCore.QTimer.singleShot(
            15 * 60 * 1000, self.query_current_status)  # 15 minutes delay

        self.heartbeat_timer = QtCore.QTimer()
        self.heartbeat_timer.setInterval(25 * 3600 * 1000)  # 25 hours
        self.heartbeat_timer.timeout.connect(self.missed_heartbeat)
        self.heartbeat_timer.start()

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

    def launch_main_app(self):
        try:
            subprocess.Popen(['sisyphus-gui-pkexec'])
        except Exception:
            pass

    @QtCore.pyqtSlot(str)
    def on_message_received(self, message):
        self.heartbeat_timer.start()

        if message == "sisyphus_exception":
            self.tray.showMessage(
                "Sisyphus Upgrade Exception",
                "An exception occurred in the Sisyphus upgrade subsystem, stopping communication.",
                QtWidgets.QSystemTrayIcon.MessageIcon.Critical
            )
        elif message == "no_internet":
            self.tray.showMessage(
                "No Internet Connection",
                "Unable to check for system upgrade because no internet connection is available.",
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning
            )

        elif message == "blocked_upgrade":
            if not self.is_ignored():
                self.tray.showMessage(
                    "Blocked Upgrade",
                    "System upgrade is available but blocked due to portage configuration issues.",
                    QtWidgets.QSystemTrayIcon.MessageIcon.Warning
                )
        elif message == "heartbeat":
            pass
        elif message == "upgrade_available":
            if not self.is_ignored():
                self.tray.showMessage(
                    "System Upgrade",
                    "System upgrade is  available to improve security, stability and performance.",
                    QtWidgets.QSystemTrayIcon.MessageIcon.Information
                )

    def missed_heartbeat(self):
        self.tray.showMessage(
            "Heartbeat Missed",
            "No heartbeat message received in over 25 hours. The daemon may be offline.",
            QtWidgets.QSystemTrayIcon.MessageIcon.Warning
        )

    def query_current_status(self):
        iface = QDBusInterface(
            "org.hermesd.MessageService",
            "/org/hermesd/MessageObject",
            "org.hermesd.MessageInterface",
            self.session_bus
        )
        reply = iface.call("GetStatus")
        if reply.type() == QDBusMessage.MessageType.ReplyMessage:
            message = reply.arguments()[0]
            self.on_message_received(message)

    def quit_app(self):
        self.tray.hide()
        self.app.quit()

    def run(self):
        self.app.exec()


if __name__ == "__main__":
    listener = SysTrayListener()
    listener.run()
