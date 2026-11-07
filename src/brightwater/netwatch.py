"""Chapter 7 proof: a run that makes no external network call.

`brightwater netwatch -- <command>` runs a command with three guards and one
witness:

* guards: every model API key is removed from its environment; HTTP(S)_PROXY
  points at a dead local port, so any client that honours proxies fails
  loudly instead of quietly reaching the internet; NO_PROXY covers loopback so
  local services still work;
* witness: every 100 ms it lists the open internet sockets of the process and
  all its children (psutil) and records each remote address. At the end it
  prints them and exits non-zero if any is not loopback.

Sampling can miss a connection that opens and closes inside one interval, so
this is evidence, not proof. The strong form, for Linux with NVIDIA, is
`compose.private.yaml`: the agent and the models on a Docker network with
`internal: true`, which has no route out at all.
"""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
import time

import psutil

SECRETS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TYPESAFE_API_KEY", "LANGFUSE_SECRET_KEY")


def _remote(conn: object) -> str | None:
    raddr = getattr(conn, "raddr", None)
    return f"{raddr.ip}:{raddr.port}" if raddr else None


def is_local(endpoint: str) -> bool:
    host = endpoint.rsplit(":", 1)[0].strip("[]")
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def run(cmd: list[str], interval: float = 0.1) -> int:
    env = {k: v for k, v in os.environ.items() if k not in SECRETS}
    env.update({"HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
                "https_proxy": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
                "NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1"})
    proc = subprocess.Popen(cmd, env=env)
    seen: dict[str, str] = {}
    root = psutil.Process(proc.pid)
    while proc.poll() is None:
        try:
            procs = [root, *root.children(recursive=True)]
        except psutil.NoSuchProcess:
            break
        for p in procs:
            try:
                for c in p.net_connections(kind="inet"):
                    ep = _remote(c)
                    if ep and ep not in seen:
                        seen[ep] = p.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        time.sleep(interval)
    external = sorted(ep for ep in seen if not is_local(ep))
    print(f"\nnetwatch: {len(seen)} remote endpoint(s) seen; {len(external)} external", file=sys.stderr)
    for ep in sorted(seen):
        print(f"  {'EXTERNAL' if ep in external else 'local   '}  {ep}  ({seen[ep]})", file=sys.stderr)
    if external:
        return 3
    return proc.returncode or 0
