#!/usr/bin/env python3
"""Show local Codex activity on a Linux VIA keyboard over VIA."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import select
import sys
import time


DEFAULT_USB_ID = "3434:0321"
VIA_DESCRIPTOR = bytes.fromhex("06 60 ff 09 61 a1 01")
GET_VALUE = 0x08
SET_VALUE = 0x07
RGB_MATRIX = 3
BRIGHTNESS, EFFECT, SPEED, COLOR = 1, 2, 3, 4
BREATHING, RAINBOW_CHEVRON, DIGITAL_RAIN = 2, 7, 17
STATUS_LIGHTING = {
    # Digital Rain supplies its own green palette and timing.
    "working": {"effect": DIGITAL_RAIN, "brightness": 96},
    "waiting": {"effect": BREATHING, "brightness": 96, "color": [0, 255], "speed": 64},
    "done": {"effect": RAINBOW_CHEVRON, "brightness": 96, "color": [0, 255], "speed": 110},
}


def load_profile(path):
    if path is None:
        return DEFAULT_USB_ID, STATUS_LIGHTING
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Profile must be a JSON object")
    usb_id = data.get("usb_id")
    if not isinstance(usb_id, str) or not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}", usb_id):
        raise ValueError("Profile usb_id must be four hex digits, a colon, and four hex digits")
    statuses = data.get("statuses")
    if not isinstance(statuses, dict) or statuses.keys() != STATUS_LIGHTING.keys():
        raise ValueError("Profile must define working, waiting, and done statuses")
    for name, lighting in statuses.items():
        if (not isinstance(lighting, dict) or not {"effect", "brightness"} <= lighting.keys()
                or lighting.keys() - {"effect", "brightness", "speed", "color"}):
            raise ValueError(f"Invalid lighting fields for {name}")
        color = lighting.get("color", [0, 0])
        if not isinstance(color, list) or len(color) != 2:
            raise ValueError(f"{name}.color must contain hue and saturation")
        values = [lighting["effect"], lighting["brightness"], lighting.get("speed", 0), *color]
        if any(type(value) is not int or not 0 <= value <= 255 for value in values):
            raise ValueError(f"Lighting values for {name} must be integers from 0 to 255")
    return usb_id.lower(), statuses


@contextmanager
def keyboard(usb_id):
    vendor, product = usb_id.upper().split(":")
    device_id = f"HID_ID=0003:0000{vendor}:0000{product}"
    found = False
    for entry in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        device = entry / "device"
        try:
            if device_id not in (device / "uevent").read_text().splitlines():
                continue
            found = True
            if not (device / "report_descriptor").read_bytes().startswith(VIA_DESCRIPTOR):
                continue
        except FileNotFoundError:
            continue
        node = Path("/dev") / entry.name
        try:
            fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as error:
            raise PermissionError(f"USB access denied for {node} ({usb_id})") from error
        except FileNotFoundError:
            continue
        try:
            yield fd, str(device.resolve())
        finally:
            os.close(fd)
        return
    if found:
        raise OSError(f"No accessible VIA Raw HID interface found for {usb_id}")
    raise OSError(f"USB keyboard {usb_id} was not found")


def exchange(fd, *data):
    report = bytes(data).ljust(32, b"\0")
    if os.write(fd, b"\0" + report) != 33:
        raise OSError("short write to VIA keyboard")
    deadline = time.monotonic() + 0.15
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], max(0, deadline - time.monotonic()))
        if not ready:
            break
        reply = os.read(fd, 32)
        if not reply or reply[0] == 0xff:
            raise OSError("VIA command unsupported by keyboard")
        # For GET, firmware replaces the values following the three-byte header.
        if len(reply) == 32 and reply.startswith(bytes(data[:3])):
            return reply
    raise OSError("VIA command timed out")


def get_lighting(fd):
    return {
        "effect": exchange(fd, GET_VALUE, RGB_MATRIX, EFFECT)[3],
        "brightness": exchange(fd, GET_VALUE, RGB_MATRIX, BRIGHTNESS)[3],
        "speed": exchange(fd, GET_VALUE, RGB_MATRIX, SPEED)[3],
        "color": list(exchange(fd, GET_VALUE, RGB_MATRIX, COLOR)[3:5]),
    }


def apply_lighting(fd, lighting):
    if "color" in lighting:
        exchange(fd, SET_VALUE, RGB_MATRIX, COLOR, *lighting["color"])
    if "speed" in lighting:
        exchange(fd, SET_VALUE, RGB_MATRIX, SPEED, lighting["speed"])
    exchange(fd, SET_VALUE, RGB_MATRIX, BRIGHTNESS, lighting["brightness"])
    exchange(fd, SET_VALUE, RGB_MATRIX, EFFECT, lighting["effect"])


def restore_lighting(fd, original):
    # This V2 firmware rounds down when it converts VIA brightness to LED output.
    apply_lighting(fd, original)
    brightness = exchange(fd, GET_VALUE, RGB_MATRIX, BRIGHTNESS)[3]
    if brightness < original["brightness"]:
        exchange(fd, SET_VALUE, RGB_MATRIX, BRIGHTNESS, min(original["brightness"] + 1, 255))


def update_sessions(sessions, event):
    session_id = event.get("session_id")
    if not session_id:
        return False
    name = event.get("hook_event_name")
    turn_id = event.get("turn_id")
    if name == "SessionEnd":
        return sessions.pop(session_id, None) is not None
    if event.get("agent_id"):
        return False

    session = sessions.get(session_id)
    if name == "UserPromptSubmit" or (
        name in {"PreModelRequest", "PermissionRequest", "PreToolUse"} and session is None
    ):
        session = {"turn": turn_id, "done": False, "approval": None, "questions": []}
        sessions[session_id] = session
    if session is None or session["turn"] != turn_id:
        return False

    if name == "Interrupt":
        del sessions[session_id]
    elif name == "Stop":
        session.update(done=True, approval=None, questions=[])
    elif name == "PermissionRequest":
        command = (event.get("tool_input") or {}).get("command")
        session.update(done=False, approval={"command": command})
    elif name == "PreToolUse":
        session["done"] = False
        call_id = event.get("tool_use_id")
        if call_id not in session["questions"]:
            session["questions"].append(call_id)
    elif name == "PostToolUse":
        if event.get("tool_name") == "request_user_input":
            call_id = event.get("tool_use_id")
            if call_id in session["questions"]:
                session["questions"].remove(call_id)
        elif session["approval"] is not None:
            command = (event.get("tool_input") or {}).get("command")
            if command == session["approval"]["command"]:
                session["approval"] = None
    elif name == "PreModelRequest":
        session.update(done=False, approval=None)
    return True


def status_for(sessions):
    if any(s["approval"] or s["questions"] for s in sessions.values()):
        return "waiting"
    if any(not s["done"] for s in sessions.values()):
        return "working"
    return "done" if sessions else None


def state_directory(usb_id):
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime) if runtime else Path.home() / ".cache"
    path = base / "codex-keychron-rgb"
    if usb_id != DEFAULT_USB_ID:
        path = path / usb_id
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def set_status(command, usb_id, statuses, event=None):
    directory = state_directory(usb_id)
    state_path = directory / "state.json"
    with (directory / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(state_path.read_text())
        except FileNotFoundError:
            state = {"sessions": {}, "original": None, "shown": None}

        if command == "hook":
            if not update_sessions(state["sessions"], event):
                return
            status = status_for(state["sessions"])
        else:
            status = None if command == "restore" else command
            if command == "restore":
                state["sessions"].clear()

        def save():
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state))
            temporary.replace(state_path)

        if status is None and state["original"] is None:
            save()
            return

        save()
        # Keep the original settings on disk before changing the keyboard.
        with keyboard(usb_id) as (fd, device):
            if state.get("device", device) != device:
                state.update(original=None, shown=None)
            state["device"] = device
            lighting = statuses.get(status)
            if status is not None and command == "hook" and state.get("applied") == lighting and state["shown"] == status:
                return
            state["shown"] = None
            if status is None and state["original"] is None:
                save()
                return
            save()
            if state["original"] is None:
                state["original"] = get_lighting(fd)
                save()
            elif "speed" not in state["original"]:
                state["original"]["speed"] = exchange(fd, GET_VALUE, RGB_MATRIX, SPEED)[3]
                save()
            if status is None:
                restore_lighting(fd, state["original"])
                state["original"] = None
            else:
                apply_lighting(fd, lighting)
            state["applied"] = lighting
            state["shown"] = status
            save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["hook", "probe", "restore", *STATUS_LIGHTING])
    parser.add_argument("--profile", type=Path, help="JSON keyboard profile")
    args = parser.parse_args()
    event = {}
    try:
        usb_id, statuses = load_profile(args.profile)
        if args.command == "probe":
            with keyboard(usb_id) as (fd, _):
                version = exchange(fd, 0x01)
                print(json.dumps({"usb_id": usb_id, "via_protocol": int.from_bytes(version[1:3], "big"),
                                  **get_lighting(fd)}))
        else:
            if args.command == "hook":
                event = json.load(sys.stdin)
                if not isinstance(event, dict):
                    return 0
            set_status(args.command, usb_id, statuses, event)
    except (OSError, ValueError) as error:
        if args.command != "hook":
            print(error, file=sys.stderr)
            return 1
    if args.command == "hook" and event.get("hook_event_name") == "Stop":
        print("{}")  # Observe events without changing Codex's decisions.
    return 0


if __name__ == "__main__":
    sys.exit(main())
