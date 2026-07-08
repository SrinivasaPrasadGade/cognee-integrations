"""
Daemonize a Cognee/uvicorn server and exit immediately.

Called by the OpenClaw cognee-openclaw plugin via runPluginCommandWithTimeout.
The wrapper exits as soon as uvicorn is launched so the TypeScript caller
returns quickly while uvicorn keeps running as a detached background process.

Usage: python boot_server.py <port>
"""

import os
import subprocess
import sys


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "8011"

    env = dict(os.environ)
    env["COGNEE_AGENT_MODE"] = "true"

    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "cognee.api.client:app", "--port", port],
        env=env,
        start_new_session=True,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Print the PID so the caller can record it in the boot lock file.
    print(p.pid)
    sys.exit(0)


if __name__ == "__main__":
    main()
