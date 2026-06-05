# Wake on Controller

A [Decky Loader](https://decky.xyz) plugin for the Steam Deck that lets you wake it from sleep by pressing the home button on any paired Bluetooth controller — just like an Xbox or PlayStation console.

## Supported controllers

Works with any Bluetooth controller that correctly advertises its HID device class:

| Controller | Home button |
|---|---|
| Xbox Series X/S, Xbox One | Xbox button (⊞) |
| PlayStation DualSense / DualShock 4 | PS button |
| Nintendo Switch Pro Controller | Home button |
| 8BitDo controllers | Home / Start |
| Steam Controller | Steam button |
| Any BT gamepad | Home / guide button |

## How it works

When you enable the plugin, it:

1. Marks the Bluetooth adapter as a wakeup source in the kernel (`/sys/class/bluetooth/hci0/device/power/wakeup`)
2. Keeps the adapter powered during suspend so it can receive signals
3. Registers your paired controller's MAC address with `btmgmt add-device` — this puts the adapter into active BLE scan mode, listening specifically for your controller
4. Installs a systemd-sleep hook (`/usr/lib/systemd/system-sleep/`) that re-arms all of the above before every suspend

When you press the home button on your controller:

```
Controller broadcasts BLE advertisement
  → BT adapter sees it (still powered, actively scanning)
  → Kernel raises wakeup interrupt
  → Steam Deck wakes up
  → Plugin reconnects the controller automatically
```

## Installation

> **Requires [Decky Loader](https://decky.xyz) to be installed first.**

### From the Decky Plugin Browser *(coming soon)*

Search for **"Wake on Controller"** in the Plugin Browser and hit Install.

### Manual sideload

Switch to Desktop Mode, open Konsole, and run:

```bash
curl -L https://github.com/steve133t/decky-wake-on-controller/releases/latest/download/wake-on-controller.zip \
  -o /tmp/wake-on-controller.zip && \
unzip -o /tmp/wake-on-controller.zip -d ~/homebrew/plugins/ && \
sudo systemctl restart plugin_loader
```

## Setup

1. **Pair your controller** — Steam button → Settings → Bluetooth → pair your controller
2. **Open the plugin** — Quick Access (⚡) → Wake on Controller
3. **Enable BT Wake** — toggle it on; the plugin auto-installs the sudoers rules and sleep hook
4. **Check the status panel** — all three dots should turn green:
   - BT Wake Armed
   - Sleep Hook installed
   - BLE Wake Scan (controller registered)
5. **Test it** — put the Deck to sleep, press the home button on your controller

If you pair a new controller after enabling the plugin, tap **Refresh Wake Devices** in the panel.

## Status indicators

| Indicator | Meaning |
|---|---|
| 🟢 BT Wake Armed | Adapter is marked as a wakeup source in the kernel |
| 🟢 Sleep Hook | systemd-sleep script installed — re-arms on every suspend |
| 🟢 BLE Wake Scan | Controller MAC registered with btmgmt for active scanning |

## Troubleshooting

**The Deck doesn't wake when I press the button**
- Make sure BLE Wake Scan is green — the controller needs to be registered before you sleep
- Some BT adapters don't support BLE wake at the hardware/firmware level; this is a known limitation on certain kernel versions
- Try tapping Refresh Wake Devices, then put the Deck to sleep again

**The plugin panel shows a loading spinner**
- Wait up to 6 seconds — the first load runs setup commands in the background
- If it never loads, check `sudo journalctl -u plugin_loader | grep -i wake` for errors

**Controller doesn't reconnect after wake**
- Tap **Reconnect Now** in the plugin panel
- Or open Steam BT settings and reconnect manually

## How the setup works automatically

On first load (and on every panel open), the plugin:
- Writes `/etc/sudoers.d/wake-on-controller` so the `deck` user can run the specific BT commands without a password
- Installs `/usr/lib/systemd/system-sleep/wake-on-controller.sh` to re-arm wake before every suspend

SteamOS updates can occasionally wipe `/etc/sudoers.d/` — re-opening the plugin panel re-installs everything automatically.

## Development

### Prerequisites

- Node.js 22+ and [pnpm](https://pnpm.io)
- A Steam Deck with Decky Loader and SSH enabled (Settings → System → Enable SSH)

### Build and sideload

```bash
git clone https://github.com/steve133t/decky-wake-on-controller
cd decky-wake-on-controller
DECK_IP=192.168.1.xx bash scripts/dev-deploy.sh
```

Or to build directly on the Deck:

```bash
DECK_IP=localhost bash scripts/dev-deploy.sh
```

### Release

```bash
git tag v1.x.x
git push origin v1.x.x
```

GitHub Actions builds the plugin zip and publishes it as a release automatically.

## License

GPL-2.0-or-later
