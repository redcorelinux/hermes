#!/usr/bin/env python3

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

SERVICE_NAME = 'org.hermesd.MessageService'
OBJECT_PATH = '/org/hermesd/MessageObject'
INTERFACE = 'org.hermesd.MessageInterface'

HEARTBEAT_INTERVAL = 2700   # 45 minutes
STATUS_INTERVAL = 21600     # 6 hours

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)


class MessageEmitter(dbus.service.Object):
    def __init__(self, bus, object_path):
        super().__init__(bus, object_path)

    @dbus.service.signal(dbus_interface=INTERFACE, signature='s')
    def MessageSent(self, message):
        logging.info(f"Signal emitted: {message}")

    @dbus.service.signal(dbus_interface=INTERFACE, signature='')
    def Heartbeat(self):
        logging.info("Heartbeat signal emitted")

    @dbus.service.method(dbus_interface=INTERFACE, in_signature='', out_signature='s')
    def GetStatus(self):
        status = get_update_status()
        logging.info(f"GetStatus called; returning: {status}")
        return status


def is_valid_url(url):
    regex = re.compile(
        r'^(http|https)://'
        r'([a-zA-Z0-9.-]+)'
        r'(\.[a-zA-Z]{2,})'
        r'(:\d+)?'
        r'(/.*)?$'
    )
    return re.match(regex, url) is not None


def check_internet():
    is_online = int()
    default_url = "https://gentoo.org"
    url = default_url

    if not is_valid_url(url):
        url = default_url

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
                open("/tmp/hermes_worlddeps.pickle", "wb"))


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

    pickle.dump([rm_list], open("/tmp/hermes_pkgrevdeps.pickle", "wb"))


def get_update_status():
    is_online = check_internet()
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
        check_update()
    except Exception:
        logging.error("Upgrade check failed!")
        return "upgrade_check_failed"

    try:
        with open("/tmp/hermes_worlddeps.pickle", "rb") as f:
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
                check_orphans()
            except Exception:
                logging.error("Orphan check failed!")
                return "orphans_check_failed"

            try:
                with open("/tmp/hermes_pkgrevdeps.pickle", "rb") as f:
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


def send_message(emitter, msg):
    logging.info(f"Emitting DBus signal: {msg}")
    emitter.MessageSent(msg)


def main():
    logging.info("Daemon starting")
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    name = dbus.service.BusName(SERVICE_NAME, bus)
    emitter = MessageEmitter(bus, OBJECT_PATH)

    loop = GLib.MainLoop()

    def send_periodic():
        status = get_update_status()
        logging.info(f"Periodic send message: {status}")
        send_message(emitter, status)
        GLib.timeout_add_seconds(STATUS_INTERVAL, send_periodic)
        return False

    def send_heartbeat():
        emitter.Heartbeat()
        GLib.timeout_add_seconds(HEARTBEAT_INTERVAL, send_heartbeat)
        return False

    def sigterm_handler(signum, frame):
        logging.info("SIGTERM received, quitting main loop")
        loop.quit()

    signal.signal(signal.SIGTERM, sigterm_handler)

    send_periodic()
    send_heartbeat()
    try:
        loop.run()
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received, quitting main loop")
        loop.quit()
    logging.info("Daemon exited cleanly")


if __name__ == '__main__':
    main()
