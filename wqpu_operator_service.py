#!/usr/bin/env python3
"""Best-effort operator autostart for the WQPU testnet stack.

Linux uses a user systemd unit. The stack itself remains responsible for process
lifecycle; the unit is a oneshot with RemainAfterExit and calls the same start/stop CLI.
"""
from __future__ import print_function

import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _quote_systemd(value):
    value = str(value)
    return '"{}"'.format(value.replace('\\', '\\\\').replace('"', '\\"'))


def _run(cmd):
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    return proc.returncode, proc.stdout.strip()


def load_json(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def build_start_args(state, network):
    args = ["start"]
    host = str(state.get("public_host") or "").strip()
    bind = str(state.get("bind_host") or "").strip()
    ports = state.get("ports") or {}
    if host:
        args += ["--public-host", host]
    if bind:
        args += ["--bind-host", bind]
    mapping = (
        ("internal_rpc", "--internal-rpc-port"),
        ("rpc", "--rpc-port"),
        ("relayer", "--relayer-port"),
        ("relay", "--relay-port"),
    )
    for key, flag in mapping:
        if ports.get(key) not in (None, ""):
            args += [flag, str(int(ports[key]))]
    public = (network or {}).get("public") or {}
    if bool(public.get("payments_enabled")):
        args.append("--payments")
    if not public.get("faucet_url"):
        args.append("--no-faucet")
    cert = str(state.get("tls_cert") or "").strip()
    key = str(state.get("tls_key") or "").strip()
    if cert and key:
        args += ["--tls-cert", cert, "--tls-key", key]
    return args


def render_systemd_unit(python_exe, script_path, start_args, home=None, path_env=None):
    home = str(home or Path.home())
    path_env = str(path_env or os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"))
    foundry = str(Path(home) / ".foundry" / "bin")
    if foundry not in path_env.split(os.pathsep):
        path_env = foundry + os.pathsep + path_env
    start_cmd = [_quote_systemd(python_exe), _quote_systemd(script_path)] + [_quote_systemd(x) for x in start_args]
    stop_cmd = [_quote_systemd(python_exe), _quote_systemd(script_path), _quote_systemd("stop")]
    return """[Unit]
Description=WQPU testnet operator stack

[Service]
Type=oneshot
RemainAfterExit=yes
Environment={home_env}
Environment={path_line}
ExecStart={start}
ExecStop={stop}
TimeoutStartSec=240
TimeoutStopSec=60

[Install]
WantedBy=default.target
""".format(
        home_env=_quote_systemd("HOME=" + home),
        path_line=_quote_systemd("PATH=" + path_env),
        start=" ".join(start_cmd),
        stop=" ".join(stop_cmd),
    )


def systemd_paths(home=None):
    home = Path(home or Path.home())
    directory = home / ".config" / "systemd" / "user"
    return directory, directory / "wqpu-testnet.service"


def _linger_status(user):
    if not shutil.which("loginctl"):
        return None
    code, out = _run(["loginctl", "show-user", user, "-p", "Linger", "--value"])
    if code != 0:
        return None
    return out.strip().lower() == "yes"


def _enable_linger_best_effort(user):
    current = _linger_status(user)
    if current is True:
        return True, "linger already enabled"
    if not shutil.which("loginctl"):
        return False, "loginctl unavailable"
    code, out = _run(["loginctl", "enable-linger", user])
    if code == 0 and _linger_status(user) is True:
        return True, "linger enabled"
    if shutil.which("sudo"):
        code, sudo_out = _run(["sudo", "-n", "loginctl", "enable-linger", user])
        if code == 0 and _linger_status(user) is True:
            return True, "linger enabled via sudo"
        out = sudo_out or out
    return False, out or "could not enable linger without interactive administrator permission"


def enable(script_path, state_path, config_path, python_exe=None, home=None):
    if not sys.platform.startswith("linux"):
        print("WQPU operator autostart: automatic service setup currently supports Linux systemd only.")
        return 2
    if not shutil.which("systemctl"):
        print("WQPU operator autostart: systemctl is unavailable.")
        return 2
    state = load_json(state_path)
    network = load_json(config_path)
    if not state or not (state.get("pids") or state.get("registry")):
        print("WQPU operator autostart: start the testnet once before enabling autostart.")
        return 2
    python_exe = str(python_exe or sys.executable)
    script_path = str(Path(script_path).resolve())
    start_args = build_start_args(state, network)
    directory, unit_path = systemd_paths(home)
    directory.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(render_systemd_unit(python_exe, script_path, start_args, home=home))
    try:
        unit_path.chmod(0o644)
    except Exception:
        pass
    code, out = _run(["systemctl", "--user", "daemon-reload"])
    if code != 0:
        print("WQPU operator autostart: systemd user manager unavailable: {}".format(out))
        return 2
    code, out = _run(["systemctl", "--user", "enable", "wqpu-testnet.service"])
    if code != 0:
        print("WQPU operator autostart: could not enable service: {}".format(out))
        return 2
    user = getpass.getuser()
    linger_ok, linger_msg = _enable_linger_best_effort(user)
    print("WQPU operator autostart enabled: {}".format(unit_path))
    if linger_ok:
        print("WQPU operator autostart: {}.".format(linger_msg))
    else:
        print("WQPU operator autostart warning: {}. Service will still start with the user systemd session.".format(linger_msg))
    return 0


def disable(home=None):
    if not sys.platform.startswith("linux"):
        return 2
    _, unit_path = systemd_paths(home)
    if shutil.which("systemctl"):
        _run(["systemctl", "--user", "disable", "wqpu-testnet.service"])
    try:
        unit_path.unlink()
    except OSError:
        pass
    if shutil.which("systemctl"):
        _run(["systemctl", "--user", "daemon-reload"])
    print("WQPU operator autostart disabled.")
    return 0


def status(home=None):
    _, unit_path = systemd_paths(home)
    enabled = False
    systemd = False
    output = ""
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        systemd = True
        code, output = _run(["systemctl", "--user", "is-enabled", "wqpu-testnet.service"])
        enabled = code == 0 and output.strip() == "enabled"
    print(json.dumps({
        "supported": sys.platform.startswith("linux"),
        "systemd_available": systemd,
        "unit": str(unit_path),
        "unit_exists": unit_path.exists(),
        "enabled": enabled,
        "detail": output,
    }, indent=2))
    return 0 if enabled else 1


def manage(action, script_path, state_path, config_path, python_exe=None, home=None):
    action = str(action or "status").lower()
    if action in ("enable", "refresh"):
        return enable(script_path, state_path, config_path, python_exe, home)
    if action == "disable":
        return disable(home)
    if action == "status":
        return status(home)
    print("unknown WQPU operator autostart action: {}".format(action))
    return 2
