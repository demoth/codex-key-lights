#!/usr/bin/env python3
"""Install or remove the user-level Codex VIA RGB hooks."""

import argparse
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import sys
import tempfile


SOURCE = Path(__file__).resolve().parent


def save_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        draft = Path(temporary.name)
        try:
            json.dump(value, temporary, indent=2)
            temporary.write("\n")
            temporary.close()
            draft.replace(path)
        finally:
            draft.unlink(missing_ok=True)


def same_group(group, template, commands):
    return any(group == {**template, "hooks": [{**template["hooks"][0], "command": command}]}
               for command in commands)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--codex-home", type=Path, default=Path(
        os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
    parser.add_argument("--profile", type=Path, help="Install a custom keyboard profile")
    args = parser.parse_args()

    home = args.codex_home.expanduser().resolve()
    destination = home / "via-rgb"
    config = home / "hooks.json"
    script = destination / "keychron-rgb.py"
    profile = destination / "profile.json"
    command = shlex.join([sys.executable, str(script), "--profile", str(profile), "hook"])
    templates = json.loads((SOURCE / "hooks.json").read_text())["hooks"]

    if args.action == "uninstall" and not config.exists():
        return
    original = json.loads(config.read_text()) if config.exists() else {}
    updated = json.loads(json.dumps(original))
    hooks = updated.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("The existing hooks.json must contain a hooks object")

    for event, (template,) in templates.items():
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"Existing {event} hooks must be an array")

        def owned(group):
            if not isinstance(group, dict):
                return False
            for handler in group.get("hooks", []):
                if not isinstance(handler, dict) or not isinstance(handler.get("command"), str):
                    continue
                try:
                    words = shlex.split(handler["command"])
                except ValueError:
                    continue
                if words[1:] in ([str(script), "--profile", str(profile), "hook"],
                                 [str(home / "keychron-rgb.py"), "hook"]):
                    return same_group(group, template, [handler["command"]])
            return False

        if args.action == "install":
            entry = {**template, "hooks": [{**template["hooks"][0], "command": command}]}
            first = next((i for i, group in enumerate(groups) if owned(group)), len(groups))
            groups = [group for group in groups if not owned(group)]
            groups.insert(first, entry)
        else:
            groups = [group for group in groups if not owned(group)]
        if groups or event in hooks:
            hooks[event] = groups

    if args.action == "install":
        profile_source = args.profile or (profile if profile.exists() else SOURCE / "example-profile.json")
        runpy.run_path(str(SOURCE / "keychron-rgb.py"))["load_profile"](profile_source)
        profile_data = json.loads(profile_source.read_text())
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / "keychron-rgb.py", script)
        save_json(profile, profile_data)
    if updated != original:
        save_json(config, updated)
    print(config)
    if args.action == "install":
        print(shlex.join([sys.executable, str(script), "--profile", str(profile), "probe"]))
    else:
        print(f"Hook entries removed. Installed script and profile are retained in {destination}.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(str(error))
