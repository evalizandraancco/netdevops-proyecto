#!/usr/bin/env python3
"""Req. 1 — Información del switch Cisco por YANG (RESTCONF o NETCONF)."""
import _bootstrap  # noqa: F401
import argparse
import json
import sys

from rich.console import Console

from netdevops.app import build_context_or_exit
from netdevops.display import cisco_view
from netdevops.errors import DeviceError


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--protocol", choices=["restconf", "netconf"], default="restconf")
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    ctx = build_context_or_exit(args.config)
    client = ctx.cisco if args.protocol == "restconf" else ctx.cisco_netconf
    console = Console()
    try:
        info = client.info()
        ifaces = client.interfaces()
    except DeviceError as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        return 1
    if args.json:
        print(json.dumps({"info": info, "interfaces": ifaces}, indent=2, ensure_ascii=False))
    else:
        console.print(cisco_view(info, ifaces))
    return 0


if __name__ == "__main__":
    sys.exit(main())
