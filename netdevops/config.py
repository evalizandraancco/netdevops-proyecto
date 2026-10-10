"""Carga de config.yaml (con valores por defecto y overrides por entorno)."""
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"

DEFAULTS: dict = {
    "cisco": {
        "host": "20.20.20.20",
        "username": "apiadmin",
        "password": "",
        "restconf_port": 443,
        "netconf_port": 830,
        # Protocolo usado para apagar/habilitar interfaces: restconf | netconf
        "control_protocol": "restconf",
        "verify_ssl": False,
        # Interfaces que NUNCA se apagan automáticamente (gestión / salida a internet)
        "protected_interfaces": ["GigabitEthernet1", "GigabitEthernet3"],
        # Capacidad (bps) a usar en lugar de la velocidad reportada. Ej: {GigabitEthernet2: 100000000}
        "capacity_override_bps": {},
    },
    "forti": {
        "host": "20.20.20.30",
        "api_key": "",
        # En FortiGate-VM no existe "wan1": se usa port2 con alias wan1
        "interface": "port2",
        "display_name": "wan1",
        "verify_ssl": False,
        "capacity_override_bps": None,
    },
    "monitor": {
        "interval_seconds": 5,
        "threshold_percent": 70.0,
        "auto_mitigate": True,
        "default_capacity_bps": 1_000_000_000,
    },
    "paths": {
        "log_file": "logs/events.log",
        "state_file": "state/mitigated.json",
    },
}


def _merge(base: dict, extra: dict | None) -> dict:
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | os.PathLike | None = None) -> dict:
    """Lee config.yaml. Variables de entorno CISCO_PASSWORD y FGT_API_KEY tienen prioridad."""
    cfg_path = Path(path or os.environ.get("NETDEVOPS_CONFIG") or DEFAULT_CONFIG)
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"No existe {cfg_path}. Copia config.example.yaml a config.yaml y complétalo."
        )
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg = _merge(deepcopy(DEFAULTS), data)

    if os.environ.get("CISCO_PASSWORD"):
        cfg["cisco"]["password"] = os.environ["CISCO_PASSWORD"]
    if os.environ.get("FGT_API_KEY"):
        cfg["forti"]["api_key"] = os.environ["FGT_API_KEY"]

    for key in ("log_file", "state_file"):
        p = Path(cfg["paths"][key])
        cfg["paths"][key] = p if p.is_absolute() else ROOT / p
    return cfg
