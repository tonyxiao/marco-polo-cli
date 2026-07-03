#!/usr/bin/env python3
import argparse
import subprocess
import sys
import time
from pathlib import Path

import frida


ADB = Path("/tmp/platform-tools-test/platform-tools/adb")
PACKAGE = "co.happybits.marcopolo"


SCRIPT = r"""
rpc.exports = {
  exportmp4: function (videoId, outputPath) {
    return new Promise(function (resolve, reject) {
      Java.perform(function () {
        try {
          var App = Java.use('co.happybits.hbmx.mp.ApplicationIntf');
          var mgr = App.getVideoPackageManager();
          var status = mgr.convertToStandardMp4(videoId, outputPath);
          resolve(String(status));
        } catch (e) {
          reject(String(e.stack || e));
        }
      });
    });
  },
  probe: function (videoId) {
    return new Promise(function (resolve, reject) {
      Java.perform(function () {
        try {
          var App = Java.use('co.happybits.hbmx.mp.ApplicationIntf');
          var mgr = App.getVideoPackageManager();
          resolve({
            hasLocalContent: Boolean(mgr.hasLocalContent(videoId)),
            isLocallyAvailable: Boolean(mgr.isLocallyAvailable(videoId)),
            isValidRecording: Boolean(mgr.isValidRecording(videoId)),
            storageSize: String(mgr.getStorageSize(videoId))
          });
        } catch (e) {
          reject(String(e.stack || e));
        }
      });
    });
  }
};
"""


def adb(*args, check=True):
    cmd = [str(ADB), *args]
    return subprocess.run(cmd, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def ensure_app_running():
    pid = adb("shell", "pidof", PACKAGE, check=False).stdout.strip()
    if pid:
        return int(pid.split()[0])
    adb("shell", "monkey", "-p", PACKAGE, "1")
    deadline = time.time() + 20
    while time.time() < deadline:
        pid = adb("shell", "pidof", PACKAGE, check=False).stdout.strip()
        if pid:
            return int(pid.split()[0])
        time.sleep(0.5)
    raise SystemExit(f"{PACKAGE} did not start")


def attach():
    device = frida.get_usb_device(timeout=5)
    pid = ensure_app_running()
    try:
        return device.attach(pid)
    except frida.ProcessNotFoundError:
        pid = ensure_app_running()
        return device.attach(pid)
    except frida.TransportError as exc:
        raise SystemExit(
            "Frida could see the device but could not attach. On this phone the stock app is "
            "not debuggable, so this requires root, a debuggable repack, or a Frida gadget build. "
            f"Underlying error: {exc}"
        )
    except frida.PermissionDeniedError as exc:
        raise SystemExit(
            "Frida attach was denied. The installed Marco Polo build is not debuggable; use a "
            "debuggable/gadget build or a rooted test device for this exact native export path. "
            f"Underlying error: {exc}"
        )


def load_exports(session):
    script = session.create_script(SCRIPT)
    script.load()
    return script.exports_sync


def pull_if_requested(device_out, host_out):
    if not host_out:
        return
    host_out.parent.mkdir(parents=True, exist_ok=True)
    adb("pull", device_out, str(host_out))


def main():
    parser = argparse.ArgumentParser(
        description="Invoke Marco Polo's native VideoPackageManager.convertToStandardMp4 via Frida."
    )
    parser.add_argument("video_id")
    parser.add_argument(
        "--device-out",
        default="/sdcard/Download/marcopolo-programmatic-export.mp4",
        help="output path on the Android device",
    )
    parser.add_argument("--pull", type=Path, help="optional host path to pull the exported MP4 to")
    parser.add_argument("--probe", action="store_true", help="only check whether the video is locally available")
    args = parser.parse_args()

    session = attach()
    try:
        exports = load_exports(session)
        probe = exports.probe(args.video_id)
        print(f"probe: {probe}")
        if args.probe:
            return
        if not probe.get("hasLocalContent"):
            raise SystemExit(f"video is not locally cached in the app: {args.video_id}")
        status = exports.exportmp4(args.video_id, args.device_out)
        print(f"convertToStandardMp4 status: {status}")
        pull_if_requested(args.device_out, args.pull)
    finally:
        session.detach()


if __name__ == "__main__":
    try:
        main()
    except frida.InvalidArgumentError as exc:
        sys.exit(str(exc))
