# Codex VIA RGB status light

Use a wired VIA keyboard's backlight to show when Codex is working, waiting for a blocking question or shell approval, or done. The hook runs locally on Linux with Python 3.9+ and uses the standard library.

The wired Keychron V2 (`3434:0321`) is the reference board, used with Codex CLI 0.170.0 and VIA protocol 11. Its preset shows Digital Rain while working, red breathing while waiting, and a moving rainbow when done. Digital Rain has a fixed green palette and timing on stock firmware.

## Install

1. From this directory, run `python3 install.py install`. The installer merges entries into your user-level `hooks.json` under `CODEX_HOME` or `~/.codex`. It prints the installed probe command.
2. Run the probe from a desktop terminal. It reads the keyboard's current settings without changing them.
3. In a new Codex session, review and trust the entries in `/hooks` in the CLI or **Settings → Hooks** in the desktop app.

The hook restores your saved lighting when the last tracked session closes normally. For a manual restore, run the printed probe command with `restore` in place of `probe`. Background questions can leave the working animation running. After an approval, the waiting effect can persist until the command returns or the next model request begins.

## Other VIA keyboards

The board needs wired VIA Raw HID and VIA RGB Matrix commands. Copy `example-profile.json`, set `usb_id` to the VID/PID reported by `lsusb`, and use the effect IDs from your board's VIA definition. Each status needs an effect ID and brightness. Speed and color are optional, and color is an array of hue and saturation. All values are integers from 0 to 255. Firmware defines which settings an effect uses.

Run `python3 keychron-rgb.py --profile my-board.json probe` first. Then try `working`, `waiting`, and `done` in place of `probe`, followed by `restore`. Install the profile with `python3 install.py install --profile my-board.json` once those effects work. Other boards are user-tested.

If your desktop account gets **USB access denied**, check your distribution's VIA/QMK udev rules. On a systemd desktop, you can also place a device-specific rule like the one below in `/etc/udev/rules.d/70-codex-via-rgb.rules`, substituting your VID/PID. Reload the rules with `sudo udevadm control --reload-rules`, replug the keyboard, and rerun the probe as your normal user.

```udev
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="3434", ATTRS{idProduct}=="0321", TAG+="uaccess"
```

## Uninstall

Close tracked Codex sessions, or run the installed `restore` command, then run `python3 install.py uninstall`. This removes the RGB hook entries and leaves the installed script and profile available for manual restoration.
