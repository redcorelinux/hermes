#!/usr/bin/env python3

import os
import sys
import signal
import subprocess
import logging
import re
import pickle
import urllib.request
from urllib.error import HTTPError, URLError

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib


class Config:
    SERVICE_NAME = 'org.hermesd.MessageService'
    OBJECT_PATH = '/org/hermesd/MessageObject'
    INTERFACE = 'org.hermesd.MessageInterface'
    HEARTBEAT_INTERVAL = 2700   # 45 minutes
    STATUS_INTERVAL = 21600     # 6 hours
    WORLDDEPS_PATH = '/tmp/hermes_worlddeps.pickle'
    PKGREVD_PATH = '/tmp/hermes_pkgrevdeps.pickle'
    DEFAULT_URL = 'https://gentoo.org'


def setup_logging():
    handlers = [logging.StreamHandler(sys.stdout)]
    logfile = os.environ.get("HERMESD_LOGFILE")
    if logfile:
        handlers.append(logging.FileHandler(logfile))
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        handlers=handlers
    )


class UpdateChecker:
    @staticmethod
    def is_valid_url(url):
        regex = re.compile(
            r'^(http|https)://'
            r'([a-zA-Z0-9.-]+)'
            r'(\.[a-zA-Z]{2,})'
            r'(:\d+)?'
            r'(/.*)?$'
        )
        return re.match(regex, url) is not None

    @staticmethod
    def check_internet(url=None):
        is_online = int()
        url = url if (url and UpdateChecker.is_valid_url(url)
                      ) else Config.DEFAULT_URL
        try:
            urllib.request.urlopen(url, timeout=5)
            is_online = int(1)
        except HTTPError as e:
            if e.code == 429:
                is_online = int(1)  # ignore rate limiting errors
            else:
                is_online = int(1)  # ignore all other http errors
        except URLError:
            is_online = int(0)

        return is_online

    @staticmethod
    def check_update():
        bin_list = []
        src_list = []
        need_cfg = int(0)

        args = ['--quiet', '--update', '--deep', '--newuse', '--pretend', '--getbinpkg', '--rebuilt-binaries',
                '--backtrack=100', '--with-bdeps=y', '--misspell-suggestion=n', '--fuzzy-search=n', '@world']

        p_exe = subprocess.Popen(
            ['emerge'] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout, stderr = p_exe.communicate()

        stdout_lines = stdout.decode('utf-8').splitlines()
        stderr_lines = stderr.decode('utf-8').splitlines()
        combined_output = stdout_lines + stderr_lines

        config_patterns = [
            r"The following .* changes are necessary to proceed",
            r"REQUIRED_USE flag constraints are unsatisfied",
            r"masked packages.*required to complete your request"
        ]

        need_cfg = int(any(
            any(re.search(p, line) for p in config_patterns)
            for line in combined_output
        ))

        for p_out in stdout_lines:
            if "[binary" in p_out:
                is_bin = p_out.split("]")[1].split("[")[0].strip()
                bin_list.append(is_bin)

            if "[ebuild" in p_out:
                is_src = p_out.split("]")[1].split("[")[0].strip()
                src_list.append(is_src)

        pickle.dump([bin_list, src_list, need_cfg],
                    open(Config.WORLDDEPS_PATH, "wb"))

    @staticmethod
    def check_orphans():
        pattern = r'(\b[a-zA-Z0-9-_]+/[a-zA-Z0-9-_]+):\s+([0-9]+(?:\.[0-9]+){0,4})(_[a-zA-Z0-9]+)?(-r[1-9][0-9]*)?'
        rm_list = []

        args = ['--quiet', '--pretend', '--verbose', '--depclean']

        p_exe = subprocess.Popen(
            ['emerge'] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout, stderr = p_exe.communicate()

        stdout_lines = stdout.decode('utf-8').splitlines()
        stderr_lines = stderr.decode('utf-8').splitlines()

        for p_out in stdout_lines:
            match = re.search(pattern, p_out)
            if match:
                to_remove = f"{match.group(1)}-{match.group(2)}"
                if match.group(3):
                    to_remove += match.group(3)
                if match.group(4):
                    to_remove += match.group(4)
                rm_list.append(to_remove)

        pickle.dump([rm_list], open(Config.PKGREVD_PATH, "wb"))

    @staticmethod
    def get_status():
        is_online = UpdateChecker.check_internet()
        if is_online != int(1):
            logging.error("Connectivity check failed!")
            return "no_internet"
        else:
            try:
                subprocess.check_call(['emerge', '--sync'])
            except Exception as e:
                logging.error(f"'emerge --sync' failed: {e}")
                return "blocked_sync"
            try:
                UpdateChecker.check_update()
            except Exception:
                logging.error("Upgrade check failed!")
                return "upgrade_check_failed"
            try:
                with open(Config.WORLDDEPS_PATH, "rb") as f:
                    bin_list, src_list, need_cfg = pickle.load(f)
            except Exception:
                logging.error("Upgrade check failed!")
                return "upgrade_check_failed"

            if need_cfg != int(0):
                logging.error("Portage configuration failure!")
                return "blocked_upgrade"
            else:
                if len(bin_list) == 0 and len(src_list) == 0:
                    try:
                        UpdateChecker.check_orphans()
                    except Exception:
                        logging.error("Orphan check failed!")
                        return "orphans_check_failed"
                    try:
                        with open(Config.PKGREVD_PATH, "rb") as f:
                            rm_list = pickle.load(f)
                    except Exception:
                        logging.error("Orphan check failed!")
                        return "orphans_check_failed"

                    if len(rm_list) == 0:
                        logging.info("System up to date!")
                        return "up_to_date"
                    else:
                        logging.info("Orphaned packages detected!")
                        return "orphans_detected"
                else:
                    logging.info("System upgrade available!")
                    return "upgrade_detected"


class MessageEmitter(dbus.service.Object):
    def __init__(self, bus, object_path):
        super().__init__(bus, object_path)

    @dbus.service.signal(dbus_interface=Config.INTERFACE, signature='s')
    def MessageSent(self, message):
        logging.info(f"Signal emitted: {message}")

    @dbus.service.signal(dbus_interface=Config.INTERFACE, signature='')
    def Heartbeat(self):
        logging.info("Heartbeat signal emitted")

    @dbus.service.method(dbus_interface=Config.INTERFACE, in_signature='', out_signature='s')
    def GetStatus(self):
        status = UpdateChecker.get_status()
        logging.info(f"GetStatus called; returning: {status}")
        return status


class HermesDaemon:
    def __init__(self):
        setup_logging()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.name = dbus.service.BusName(Config.SERVICE_NAME, self.bus)
        self.emitter = MessageEmitter(self.bus, Config.OBJECT_PATH)
        self.loop = GLib.MainLoop()
        self._setup_signals()

    def _setup_signals(self):
        signal.signal(signal.SIGTERM, self._sigterm_handler)
        signal.signal(signal.SIGINT, self._sigterm_handler)

    def _sigterm_handler(self, signum, frame):
        logging.info("SIGTERM received, quitting main loop")
        self.loop.quit()

    def _send_periodic(self):
        status = UpdateChecker.get_status()
        logging.info(f"Periodic send message: {status}")
        self.emitter.MessageSent(status)
        GLib.timeout_add_seconds(Config.STATUS_INTERVAL, self._send_periodic)
        return True

    def _send_heartbeat(self):
        self.emitter.Heartbeat()
        GLib.timeout_add_seconds(
            Config.HEARTBEAT_INTERVAL, self._send_heartbeat)
        return True

    def run(self):
        logging.info("Daemon starting")
        self._send_periodic()
        self._send_heartbeat()
        try:
            self.loop.run()
        except KeyboardInterrupt:
            logging.info("Keyboard interrupt received, quitting main loop")
            self.loop.quit()
        logging.info("Daemon exited cleanly")


def main():
    daemon = HermesDaemon()
    daemon.run()


if __name__ == '__main__':
    main()
