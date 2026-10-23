"""Fixed disposable OS test helper; no application/DB/network behavior."""

import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from linux_transport import PrivateDirectory  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    directory = PrivateDirectory(sys.argv[1])
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    directory.create("os-child-ready.json", b'{"synthetic":true}')
    directory.close()
    time.sleep(60)
