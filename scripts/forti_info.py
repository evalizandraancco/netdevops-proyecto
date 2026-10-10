#!/usr/bin/env python3
"""Req. 1 — Información del FortiGate y de la interfaz wan1 por API REST."""
import _bootstrap  # noqa: F401
import argparse
import json
import sys

from rich.console import Console

from netdevops.app import build_context_or_exit
from netdevops.display import forti_view
from netdevops.errors import DeviceError


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    ctx = build_context_or_exit(args.config)
    console = Console()
    try:
        status = ctx.forti.status()
        iface = ctx.forti.interface()
    except DeviceError as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        return 1
    if args.json:
        print(json.dumps({"status": status, "interface": iface}, indent=2, ensure_ascii=False))
    else:
        console.print(forti_view(status, iface, ctx.cfg["forti"]["display_name"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
