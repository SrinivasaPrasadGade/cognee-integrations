"""
ensure_and_boot.py — Cognee install + server bootstrap for the openclaw plugin.

Responsibilities:
  1. Create ~/.cognee-plugin/venv and install cognee if absent
     (uv preferred; falls back to stdlib venv + pip)
  2. Boot uvicorn from the venv as a detached OS daemon

Self-daemonizes immediately so the caller (runPluginCommandWithTimeout)
returns in < 1 s regardless of install time.  The actual install + boot
happen in the background daemon; the TypeScript health-poll loop will pick
up the server once it is ready.

Usage:
  python ensure_and_boot.py [port]          # spawns daemon, exits fast
  python ensure_and_boot.py [port] --daemon  # daemon body (internal)
"""

import json
import os
import subprocess
import sys
import time

BASE = os.path.join(os.path.expanduser("~"), ".cognee-plugin")
VENV_DIR = os.path.join(BASE, "venv")
_bin = "Scripts" if os.name == "nt" else "bin"
_ext = ".exe" if os.name == "nt" else ""
VENV_PYTHON = os.path.join(VENV_DIR, _bin, "python" + _ext)
PIP = os.path.join(VENV_DIR, _bin, "pip" + _ext)
UV_DIR = os.path.join(BASE, "uv")
UV_BIN = os.path.join(UV_DIR, "uv" + _ext)
READY_MARKER = os.path.join(BASE, ".venv-ready.json")
INSTALL_LOCK = os.path.join(BASE, "venv-install.lock")
COGNEE_VERSION = "1.2.2.dev3"


# ---------------------------------------------------------------------------
# Self-daemonize
# ---------------------------------------------------------------------------
if "--daemon" not in sys.argv:
    port_arg = sys.argv[1] if len(sys.argv) > 1 else "8011"
    p = subprocess.Popen(
        [sys.executable, __file__, port_arg, "--daemon"],
        start_new_session=True,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(p.pid)
    sys.exit(0)

# ---------------------------------------------------------------------------
# Daemon body
# ---------------------------------------------------------------------------
PORT = next((a for a in sys.argv[1:] if a != "--daemon"), "8011")


def pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def acquire_lock(path: str, stale_secs: float = 720.0) -> bool:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    now = time.time()
    if os.path.exists(path):
        try:
            d = json.loads(open(path).read())
            pid = int(d.get("pid", 0) or 0)
            created = float(d.get("created_at", 0) or 0)
            if pid_alive(pid) and now - created < stale_secs:
                return False  # another live process holds the lock
            os.unlink(path)
        except Exception:
            pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": os.getpid(), "created_at": now}, f)
        return True
    except FileExistsError:
        return False


def release_lock(path: str) -> None:
    try:
        os.unlink(path)
    except Exception:
        pass


def install_cognee() -> bool:
    """Create venv and install pinned cognee version. Single-flighted."""
    os.makedirs(BASE, exist_ok=True)
    if not acquire_lock(INSTALL_LOCK):
        # Another process won the install lock; wait for it to finish.
        deadline = time.monotonic() + 720
        while time.monotonic() < deadline:
            if os.path.exists(VENV_PYTHON):
                return True
            time.sleep(0.5)
        return os.path.exists(VENV_PYTHON)
    try:
        import shutil

        uv = UV_BIN if os.path.exists(UV_BIN) else (shutil.which("uv") or "")
        if not uv:
            try:
                uv_env = os.environ.copy()
                uv_env["UV_UNMANAGED_INSTALL"] = UV_DIR
                os.makedirs(UV_DIR, exist_ok=True)
                subprocess.run(
                    ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
                    env=uv_env,
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                if os.path.exists(UV_BIN):
                    uv = UV_BIN
            except Exception:
                pass

        if uv:
            uv_env = os.environ.copy()
            if not os.path.exists(VENV_PYTHON):
                subprocess.run(
                    [uv, "venv", VENV_DIR, "--python", "3.12"],
                    env=uv_env,
                    check=True,
                    capture_output=True,
                    timeout=300,
                )
            subprocess.run(
                [
                    uv,
                    "pip",
                    "install",
                    "--upgrade",
                    "--python",
                    VENV_PYTHON,
                    f"cognee=={COGNEE_VERSION}",
                ],
                env=uv_env,
                check=True,
                capture_output=True,
                timeout=600,
            )
        elif not os.path.exists(VENV_PYTHON):
            # Last-resort: stdlib venv + pip (slower; requires system python ≥ 3.10)
            subprocess.run(
                [sys.executable, "-m", "venv", VENV_DIR],
                check=True,
                capture_output=True,
                timeout=120,
            )
            subprocess.run(
                [PIP, "install", "--upgrade", f"cognee=={COGNEE_VERSION}"],
                check=True,
                capture_output=True,
                timeout=600,
            )

        # Write ready marker so other tools can detect install completion.
        tmp = READY_MARKER + ".tmp"
        with open(tmp, "w") as f:
            json.dump(
                {
                    "cognee_version": COGNEE_VERSION,
                    "python": VENV_PYTHON,
                    "updated_at": time.time(),
                },
                f,
            )
        os.replace(tmp, READY_MARKER)
        return True
    except Exception:
        return False
    finally:
        release_lock(INSTALL_LOCK)


def boot_server() -> None:
    if not os.path.exists(VENV_PYTHON):
        return
    env = dict(os.environ)
    env["COGNEE_AGENT_MODE"] = "true"
    subprocess.Popen(
        [VENV_PYTHON, "-m", "uvicorn", "cognee.api.client:app", "--port", PORT],
        env=env,
        start_new_session=True,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


if not os.path.exists(VENV_PYTHON):
    install_cognee()

boot_server()
