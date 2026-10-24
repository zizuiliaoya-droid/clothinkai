"""Disposable Linux kernel test child; no filesystem or network access."""

import sys
import time

if len(sys.argv) != 2 or sys.argv[1] not in {"small", "oversize", "sleep"}:
    raise SystemExit(2)
if sys.argv[1] == "small":
    print('{"fixture":true}')
elif sys.argv[1] == "oversize":
    sys.stdout.write("x" * 100000)
    sys.stdout.flush()
else:
    time.sleep(60)
