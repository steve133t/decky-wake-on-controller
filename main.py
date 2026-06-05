import asyncio
import logging
import os
import subprocess
import json
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WakeOnController")

SLEEP_HOOK_PATH = "/usr/lib/systemd/system-sleep/wake-on-controller.sh"
SUDOERS_PATH = "/etc/sudoers.d/wake-on-controller"
BT_ADAPTER = "hci0"

# Any BT device that bluez classifies as a gaming input is eligible.
# bluetoothctl sets Icon: input-gaming for gamepads based on HID device class —
# this covers Xbox, DualSense, Switch Pro, 8BitDo, Steam Controller, etc.
BT_GAMEPAD_ICON = "input-gaming"

SETTINGS_PATH     = Path("/home/deck/.config/wake-on-controller/settings.json")
WAKE_DEVICES_PATH = Path("/home/deck/.config/wake-on-controller/wake-devices.txt")

# Embedded sudoers rules — written once on first load.
# On SteamOS the deck user already has NOPASSWD:ALL, so we can bootstrap this
# by running `sudo tee` on first load without any pre-existing custom rules.
SUDOERS_CONTENT = """\
# Wake on Controller — Decky plugin
# Allows the deck user to arm BT wake and manage the sleep hook without a password.
deck ALL=(root) NOPASSWD: /bin/sh -c echo * > /sys/class/bluetooth/hci0/device/power/*
deck ALL=(root) NOPASSWD: /usr/bin/tee /usr/lib/systemd/system-sleep/wake-on-controller.sh
deck ALL=(root) NOPASSWD: /bin/rm -f /usr/lib/systemd/system-sleep/wake-on-controller.sh
deck ALL=(root) NOPASSWD: /bin/chmod +x /usr/lib/systemd/system-sleep/wake-on-controller.sh
deck ALL=(root) NOPASSWD: /usr/bin/btmgmt wake-system *
deck ALL=(root) NOPASSWD: /usr/bin/btmgmt add-device *
deck ALL=(root) NOPASSWD: /usr/bin/btmgmt del-device *
"""


def _run(cmd: list[str], check=True, timeout=8) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning(f"WakeOnController: command timed out: {cmd}")
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="timeout")


def _run_root(cmd: list[str], stdin: str = "", timeout=10) -> subprocess.CompletedProcess:
    """
    Run a command as root.
    - If already root (Decky may run backend as root): execute directly.
    - Otherwise use `sudo -S` which reads the password from stdin.
      For a NOPASSWD user like deck this bypasses the requiretty check
      without needing an actual password — passing an empty string is fine.
    """
    try:
        if os.geteuid() == 0:
            return subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                                  check=False, timeout=timeout)
        return subprocess.run(
            ["sudo", "-S"] + cmd,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"WakeOnController: root command timed out: {cmd}")
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="timeout")


def _sysfs_write(path: str, value: str) -> bool:
    result = _run_root(["tee", path])
    if result.returncode != 0:
        # tee needs input — use sh -c
        result = _run_root(["sh", "-c", f"echo '{value}' > {path}"])
    return result.returncode == 0


def _adapter_sysfs_path() -> str | None:
    """
    Find the sysfs path for the first available BT adapter.

    On some hardware (e.g. Steam Deck's integrated BT) the `/device`
    subdirectory doesn't exist — the power management files sit directly
    under `/sys/class/bluetooth/hci0/`. Try several candidates in order.
    """
    import glob as _glob
    candidates = []
    # Prefer the explicitly configured adapter, then any other hci*
    for hci in [BT_ADAPTER] + [p.split("/")[-1]
                                for p in _glob.glob("/sys/class/bluetooth/hci*")
                                if p.split("/")[-1] != BT_ADAPTER]:
        hci_base = f"/sys/class/bluetooth/{hci}"
        if not os.path.exists(hci_base):
            continue
        # Try with /device first (external USB/PCIe BT dongles)
        device_path = f"{hci_base}/device"
        if os.path.exists(device_path):
            return device_path
        # Fall back to the hci path itself (integrated BT, Steam Deck)
        return hci_base
    return None


def _sysfs_power_path(adapter_path: str, filename: str) -> str | None:
    """Return the full path to a power sysfs file, or None if it doesn't exist."""
    for candidate in [
        f"{adapter_path}/power/{filename}",        # hci0 direct (Steam Deck)
        f"{adapter_path}/{filename}",               # edge case
    ]:
        if os.path.exists(candidate):
            return candidate
    return None


def _find_usb_controllers() -> list[dict]:
    """
    Find USB/dongle-connected gamepads via /sys/class/input/js* joystick nodes.

    For each joystick device that lives on the USB bus (not Bluetooth), walks
    up the sysfs tree to find the USB device node that has a power/wakeup file.
    That node is what we need to enable for USB HID wake.

    This is experimental — not all USB hubs/controllers pass wakeup events
    to the host, and some hardware requires BIOS/firmware support.
    """
    import glob as _glob
    found = []
    seen_wakeup_paths: set[str] = set()

    for js_path in sorted(_glob.glob("/sys/class/input/js*")):
        real = os.path.realpath(js_path)
        # Skip Bluetooth HID devices — those are handled separately
        if "bluetooth" in real.lower():
            continue
        # Only USB devices (path contains /usb or the name of a USB subsystem)
        if "usb" not in real.lower():
            continue

        # Walk up the path to find the nearest USB device with power/wakeup
        parts = real.rstrip("/").split("/")
        wakeup_path = None
        for i in range(len(parts), 0, -1):
            candidate = "/".join(parts[:i]) + "/power/wakeup"
            if os.path.exists(candidate):
                wakeup_path = candidate
                break

        if not wakeup_path or wakeup_path in seen_wakeup_paths:
            continue
        seen_wakeup_paths.add(wakeup_path)

        # Try to get a human-readable name
        name = os.path.basename(js_path)  # fallback: "js0"
        for name_file in [
            f"{js_path}/device/name",
            f"{js_path}/../name",
        ]:
            try:
                name = Path(name_file).read_text().strip()
                break
            except Exception:
                pass

        # Check current wakeup state
        try:
            armed = Path(wakeup_path).read_text().strip() == "enabled"
        except Exception:
            armed = False

        found.append({
            "js":          os.path.basename(js_path),
            "name":        name,
            "wakeup_path": wakeup_path,
            "armed":       armed,
        })

    return found


def _enable_usb_wake(enable: bool) -> int:
    """Enable or disable USB HID wakeup for all detected USB gamepads."""
    value = "enabled" if enable else "disabled"
    count = 0
    for ctrl in _find_usb_controllers():
        r = _run_root(["sh", "-c", f"echo {value} > {ctrl['wakeup_path']}"])
        if r.returncode == 0:
            count += 1
            logger.info(f"WakeOnController: USB wake {value} for {ctrl['name']}")
        else:
            logger.warning(f"WakeOnController: could not set USB wake for {ctrl['name']}: {r.stderr}")
    return count


class Plugin:

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def _main(self):
        logger.info("WakeOnController: loading")
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        await self._auto_setup(self)
        if await self.get_enabled(self):
            await self._apply_bt_wake(self, enable=True)

    async def _unload(self):
        logger.info("WakeOnController: unloading")

    # Decky calls these on system suspend/resume
    async def _on_suspend(self):
        """Re-arm BT and USB wakeup right before the system sleeps."""
        logger.info("WakeOnController: suspend hook — re-arming wake sources")
        if await self.get_enabled(self):
            await self._apply_bt_wake(self, enable=True)
            await self._register_devices_for_wake(self)
            _enable_usb_wake(True)   # re-arm USB HID wake (experimental)

    async def _on_resume(self):
        """After wake: reconnect controller and clear wake scan list."""
        logger.info("WakeOnController: resume hook — reconnecting controller")
        if await self.get_enabled(self):
            await self._unregister_devices_for_wake(self)
            await self._reconnect_controller(self)

    # ------------------------------------------------------------------ #
    #  Public API (callable from the frontend)
    # ------------------------------------------------------------------ #

    async def initialize(self) -> dict:
        """
        Called by the frontend when the panel opens.
        Idempotent — safe to call every time the UI mounts.
        Always returns a status dict even if setup partially fails.
        """
        try:
            await self._auto_setup(self)
        except Exception as e:
            logger.error(f"WakeOnController: auto_setup error (non-fatal): {e}")
        return await self.get_status(self)

    async def get_enabled(self) -> bool:
        s = self._load_settings(self)
        return s.get("enabled", False)

    async def set_enabled(self, enabled: bool) -> dict:
        s = self._load_settings(self)
        s["enabled"] = enabled
        self._save_settings(self, s)
        ok = await self._apply_bt_wake(self, enable=enabled)
        _enable_usb_wake(enabled)   # best-effort, experimental
        if enabled and ok:
            await self._install_sleep_hook(self)
            await self._register_devices_for_wake(self)
        elif not enabled:
            await self._unregister_devices_for_wake(self)
            await self._remove_sleep_hook(self)
        return {"success": ok}

    async def get_status(self) -> dict:
        """
        Return a snapshot of everything the UI needs.

        Deliberately fast — no bluetoothctl scans here (they're slow).
        Controller list is read from wake-devices.txt (written at enable-time)
        rather than queried live. The Refresh button triggers a live scan.
        """
        # btmgmt info: fast (~0.3s), tells us adapter presence + wake state
        bt_info = _run(["btmgmt", "info"], check=False, timeout=4)
        adapter_found = bt_info.returncode == 0
        wakeup_armed  = "wake-system" in bt_info.stdout

        # Read registered controllers from the saved file — no BT scan needed
        registered = self._load_wake_devices(self)

        # USB controllers: pure filesystem reads, no subprocess
        usb_controllers = _find_usb_controllers()

        return {
            "enabled":               await self.get_enabled(self),
            "adapter_found":         adapter_found,
            "wakeup_armed":          wakeup_armed,
            "power_control":         "on" if wakeup_armed else "auto",
            "controllers":           registered,
            "usb_controllers":       usb_controllers,
            "sleep_hook_installed":  os.path.exists(SLEEP_HOOK_PATH),
            "wake_devices_registered": len(registered) > 0,
        }

    def _load_wake_devices(self) -> list[dict]:
        """
        Read controller info from wake-devices.txt — zero subprocess calls.

        File format (written by _register_devices_for_wake):
          AA:BB:CC:DD:EE:FF\tController Name
        Legacy format (MAC only, no tab) is also handled.
        """
        if not WAKE_DEVICES_PATH.exists():
            return []
        controllers = []
        for line in WAKE_DEVICES_PATH.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            if "\t" in line:
                mac, name = line.split("\t", 1)
            else:
                mac, name = line, line   # legacy: show MAC as name
            controllers.append({"mac": mac.strip(), "name": name.strip(), "connected": False})
        return controllers

    async def get_paired_controllers(self) -> list[dict]:
        return await self._list_bt_controllers(self)

    async def refresh_wake_devices(self) -> dict:
        """
        Re-scan paired controllers and re-register them immediately.
        Useful after pairing a new controller without needing to toggle
        the plugin off and on or wait for the next suspend.
        """
        ok = await self._register_devices_for_wake(self)
        return {"success": ok, "status": await self.get_status(self)}

    async def test_reconnect(self) -> dict:
        ok = await self._reconnect_controller(self)
        return {"success": ok}

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    async def _auto_setup(self) -> None:
        """
        One-time idempotent setup: install sudoers rules and the sleep hook.
        Runs on plugin load and every time the panel opens so nothing falls
        through the cracks (e.g. after a SteamOS update wipes /etc/sudoers.d/).
        """
        await self._ensure_sudoers(self)
        await self._ensure_sleep_hook(self)

    async def _ensure_sudoers(self) -> bool:
        """Write the sudoers file if it doesn't exist or is out of date."""
        existing = ""
        if os.path.exists(SUDOERS_PATH):
            try:
                existing = Path(SUDOERS_PATH).read_text()
            except Exception:
                pass

        if existing.strip() == SUDOERS_CONTENT.strip():
            return True  # already current

        # SteamOS deck user has NOPASSWD:ALL by default, so this sudo call
        # works without pre-existing custom rules.
        proc = _run_root(["tee", SUDOERS_PATH], stdin=SUDOERS_CONTENT)
        if proc.returncode != 0:
            logger.error(f"WakeOnController: failed to write sudoers: {proc.stderr}")
            return False

        # Validate the file so a syntax error doesn't lock sudo
        check = _run_root(["visudo", "-c", "-f", SUDOERS_PATH])
        if check.returncode != 0:
            logger.error(f"WakeOnController: sudoers validation failed, removing: {check.stderr}")
            _run_root(["rm", "-f", SUDOERS_PATH])
            return False

        _run_root(["chmod", "0440", SUDOERS_PATH])
        logger.info("WakeOnController: sudoers rules installed")
        return True

    async def _ensure_sleep_hook(self) -> bool:
        """Install the sleep hook if it's not already in place."""
        if os.path.exists(SLEEP_HOOK_PATH):
            return True
        return await self._install_sleep_hook(self)

    async def _register_devices_for_wake(self) -> bool:
        """
        Tell the BT adapter to actively scan for each paired Xbox controller
        during suspend and wake the system when it sees one.

        Two steps:
          1. btmgmt add-device -a 0x01 -A 0x02 <mac>
             Registers the MAC as an LE (BLE) auto-connect target.
             When the Xbox button is pressed the controller broadcasts a BLE
             advertisement; the adapter sees it and raises a wakeup interrupt.
          2. Save the MAC list to a file so the sleep hook bash script can
             re-register them on every suspend without needing Python running.
        """
        controllers = await self._list_bt_controllers(self)
        if not controllers:
            logger.warning("WakeOnController: no Xbox controllers found to register for wake")
            return False

        # Persist MAC + Name for the sleep hook and fast status reads
        WAKE_DEVICES_PATH.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{c['mac']}\t{c['name']}" for c in controllers]
        WAKE_DEVICES_PATH.write_text("\n".join(lines) + "\n")

        macs = [c["mac"] for c in controllers]

        success = False
        for mac in macs:
            # 0x01 = LE Public address type  (BLE advertisement from Xbox button press)
            # 0x02 = auto-connect action     (kernel wakes system when device is seen)
            r = _run_root(["btmgmt", "add-device", "-a", "0x01", "-A", "0x02", mac])
            if r.returncode == 0:
                logger.info(f"WakeOnController: registered {mac} for BLE wake scan")
                success = True
            else:
                logger.warning(f"WakeOnController: btmgmt add-device failed for {mac}: {r.stderr.strip()}")

        return success

    async def _unregister_devices_for_wake(self) -> None:
        """
        Remove controllers from the BT auto-connect/wake list after the system
        has already woken up — keeps the adapter from scanning unnecessarily
        while the Deck is in active use.
        """
        if not WAKE_DEVICES_PATH.exists():
            return

        for mac in WAKE_DEVICES_PATH.read_text().splitlines():
            mac = mac.strip()
            if mac:
                _run_root(["btmgmt", "del-device", mac])

        WAKE_DEVICES_PATH.unlink(missing_ok=True)
        logger.info("WakeOnController: unregistered wake devices after resume")

    async def _apply_bt_wake(self, enable: bool) -> bool:
        adapter_path = _adapter_sysfs_path()
        if not adapter_path:
            logger.warning("WakeOnController: BT adapter sysfs path not found")
            return False

        value = "enabled" if enable else "disabled"

        # 1. Set wakeup source — find the actual sysfs path first
        wakeup_path = _sysfs_power_path(adapter_path, "wakeup")
        if not wakeup_path:
            # Path doesn't exist yet; try the most likely location
            wakeup_path = f"{adapter_path}/power/wakeup"
        r1 = _run_root(["sh", "-c", f"echo {value} > {wakeup_path}"])

        # 2. Prevent adapter from auto-suspending when we want wake enabled
        power_ctrl_val = "on" if enable else "auto"
        ctrl_path = _sysfs_power_path(adapter_path, "control")
        if not ctrl_path:
            ctrl_path = f"{adapter_path}/power/control"
        r2 = _run_root(["sh", "-c", f"echo {power_ctrl_val} > {ctrl_path}"])

        # 3. Tell btmgmt to allow wake-system (best-effort — not all kernels support it)
        _run_root(["btmgmt", "wake-system", "on" if enable else "off"])

        success = (r1.returncode == 0) and (r2.returncode == 0)
        logger.info(f"WakeOnController: _apply_bt_wake({enable}) -> {success}")
        return success

    async def _install_sleep_hook(self) -> bool:
        """
        Write a systemd-sleep hook script so BT wake is re-armed every time
        the deck suspends (in case power management resets it).
        """
        script = """#!/bin/bash
# Installed by the Wake on Controller Decky plugin
# Runs before every suspend to keep the BT adapter armed for wake.
#
# Why this exists: power management can reset the sysfs wakeup flags when
# the system suspends, so we re-apply them here at the last moment before
# the system actually goes to sleep. We also re-register each paired Xbox
# controller so the adapter actively scans for the BLE advertisement that
# the controller sends when the Xbox button is pressed.

SYSFS=$(ls -d /sys/class/bluetooth/hci*/device 2>/dev/null | head -1)
[ -z "$SYSFS" ] && SYSFS=$(ls -d /sys/class/bluetooth/hci* 2>/dev/null | head -1)
WAKE_DEVICES="/home/deck/.config/wake-on-controller/wake-devices.txt"

case "$1" in
  pre)
    # 1. Keep adapter powered and mark it as a wakeup source
    if [ -n "$SYSFS" ]; then
      echo enabled > "$SYSFS/power/wakeup" 2>/dev/null || true
      echo on      > "$SYSFS/power/control" 2>/dev/null || true
    fi

    # 2. Allow the adapter to wake the system
    btmgmt wake-system on

    # 3. Register each paired controller for active BLE wake scanning
    #    -a 0x01 = LE Public address (the type Xbox controllers advertise on)
    #    -A 0x02 = auto-connect action (triggers wakeup when advertisement seen)
    if [ -f "$WAKE_DEVICES" ]; then
      while IFS=$'\t' read -r mac _rest; do
        [ -z "$mac" ] && continue
        btmgmt add-device -a 0x01 -A 0x02 "$mac"
      done < "$WAKE_DEVICES"
    fi
    ;;
esac
"""
        result = _run_root(["sh", "-c", f"cat > {SLEEP_HOOK_PATH} << 'HOOK_EOF'\n{script}\nHOOK_EOF"])
        if result.returncode != 0:
            # Fallback: write via tee
            proc = _run_root(["tee", SLEEP_HOOK_PATH], stdin=script)
            if proc.returncode != 0:
                logger.error(f"WakeOnController: failed to write sleep hook: {proc.stderr}")
                return False

        _run_root(["chmod", "+x", SLEEP_HOOK_PATH])
        logger.info("WakeOnController: sleep hook installed")
        return True

    async def _remove_sleep_hook(self) -> bool:
        if os.path.exists(SLEEP_HOOK_PATH):
            result = _run_root(["rm", "-f", SLEEP_HOOK_PATH])
            return result.returncode == 0
        return True

    async def _list_bt_controllers(self) -> list[dict]:
        """
        Return all paired BT devices that bluez classifies as gaming input.
        Uses the Icon field from `bluetoothctl info` which is derived from the
        HID device class — covers Xbox, DualSense, Switch Pro, 8BitDo, etc.
        """
        try:
            result = _run(["bluetoothctl", "devices"])
            controllers = []
            for line in result.stdout.splitlines():
                # Format: "Device AA:BB:CC:DD:EE:FF <Name>"
                parts = line.strip().split(" ", 2)
                if len(parts) < 3:
                    continue
                mac  = parts[1]
                name = parts[2]
                info = _run(["bluetoothctl", "info", mac], check=False, timeout=3)
                if f"Icon: {BT_GAMEPAD_ICON}" not in info.stdout:
                    continue
                connected = "Connected: yes" in info.stdout
                controllers.append({"mac": mac, "name": name, "connected": connected})
            return controllers
        except Exception as e:
            logger.error(f"WakeOnController: error listing controllers: {e}")
            return []

    async def _reconnect_controller(self) -> bool:
        """After wake, tell bluetoothctl to connect known Xbox controllers."""
        controllers = await self._list_bt_controllers(self)
        if not controllers:
            return False
        success = False
        for ctrl in controllers:
            if not ctrl["connected"]:
                result = _run(["bluetoothctl", "connect", ctrl["mac"]], check=False)
                if result.returncode == 0:
                    success = True
                    logger.info(f"WakeOnController: reconnected {ctrl['name']}")
        return success

    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            return {}

    def _save_settings(self, settings: dict):
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
