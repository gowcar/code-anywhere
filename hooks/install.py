#!/usr/bin/env python3
"""安装 Hook 到 Claude Code settings.json"""

import json
import os
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.resolve()
SETTINGS_FILE = Path.home() / ".claude" / "settings.json"


def install():
    # 读取现有配置
    if SETTINGS_FILE.exists():
        settings = json.loads(SETTINGS_FILE.read_text())
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})

    hook_configs = {
        "SessionStart": {
            "matcher": "",
            "hooks": [{"type": "command", "command": f"python3 {HOOKS_DIR}/session_start.py"}],
        },
        "Stop": {
            "matcher": "",
            "hooks": [{"type": "command", "command": f"python3 {HOOKS_DIR}/stop.py"}],
        },
        "Notification": {
            "matcher": "",
            "hooks": [{"type": "command", "command": f"python3 {HOOKS_DIR}/notification.py"}],
        },
        "PreToolUse": {
            "matcher": "",
            "hooks": [{"type": "command", "command": f"python3 {HOOKS_DIR}/permission.py", "timeout": 130}],
        },
    }

    for event_name, config in hook_configs.items():
        existing = hooks.get(event_name, [])
        # 检查是否已安装（避免重复）
        cmd = config["hooks"][0]["command"]
        already = any(
            cmd in (h.get("hooks", [{}])[0].get("command", "") if h.get("hooks") else "")
            for h in existing
        )
        if not already:
            existing.append(config)
            hooks[event_name] = existing
            print(f"  ✓ {event_name}")
        else:
            print(f"  - {event_name} (已安装)")

    settings["hooks"] = hooks
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
    print(f"\n配置已写入 {SETTINGS_FILE}")


def uninstall():
    if not SETTINGS_FILE.exists():
        print("未找到 settings.json")
        return

    settings = json.loads(SETTINGS_FILE.read_text())
    hooks = settings.get("hooks", {})
    hooks_dir_str = str(HOOKS_DIR)

    for event_name in list(hooks.keys()):
        hooks[event_name] = [
            h for h in hooks[event_name]
            if hooks_dir_str not in json.dumps(h)
        ]
        if not hooks[event_name]:
            del hooks[event_name]

    settings["hooks"] = hooks
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
    print("Hook 已卸载")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
        uninstall()
    else:
        print("安装 Code Anywhere Hooks...\n")
        install()
