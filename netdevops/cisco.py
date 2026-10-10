"""Cliente Cisco IOS-XE por RESTCONF y NETCONF usando modelos YANG (Req. 1).

Modelos usados:
  * ietf-interfaces (estado, velocidad, contadores, habilitar/deshabilitar)
  * ietf-ip (direcciones IPv4)
  * Cisco-IOS-XE-native (hostname, versión)
  * Cisco-IOS-XE-device-hardware-oper (hora de arranque -> uptime)
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import quote

import requests
import urllib3

from .errors import DeviceError

NS_IF = "urn:ietf:params:xml:ns:yang:ietf-interfaces"
NS_NATIVE = "http://cisco.com/ns/yang/Cisco-IOS-XE-native"


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _interface_row(state: dict, cfg: dict | None = None) -> dict:
    stats = state.get("statistics", {}) or {}
    cfg = cfg or {}
    addrs = (cfg.get("ietf-ip:ipv4") or {}).get("address") or []
    return {
        "name": state.get("name"),
        "admin_status": state.get("admin-status", "unknown"),
        "oper_status": state.get("oper-status", "unknown"),
        "speed_bps": _int(state.get("speed")),
        "in_octets": _int(stats.get("in-octets")),
        "out_octets": _int(stats.get("out-octets")),
        "description": cfg.get("description", ""),
        "ip": addrs[0].get("ip", "") if addrs else "",
    }


class CiscoRestconf:
    """RESTCONF sobre HTTPS (puerto 443)."""

    def __init__(self, host, username, password, port=443, verify=False, timeout=10):
        self.host = host
        self.base = f"https://{host}:{port}/restconf/data"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = verify
        self.session.trust_env = False  # ignora proxies/CA del entorno: el laboratorio es local
        self.headers = {
            "Accept": "application/yang-data+json",
            "Content-Type": "application/yang-data+json",
        }
        if not verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # -- bajo nivel -----------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base}/{path}"
        try:
            resp = self.session.request(
                method, url, headers=self.headers, timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise DeviceError(f"Cisco {self.host} no responde por RESTCONF: {exc}") from exc
        if resp.status_code >= 400:
            raise DeviceError(
                f"Cisco RESTCONF {method} {path} -> HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return resp

    def _get(self, path: str) -> dict:
        try:
            return self._request("GET", path).json()
        except ValueError as exc:
            raise DeviceError(f"Respuesta no JSON en {path}") from exc

    # -- información general (Req. 1) ----------------------------------
    def hostname(self) -> str:
        return self._get("Cisco-IOS-XE-native:native/hostname")["Cisco-IOS-XE-native:hostname"]

    def native_version(self) -> str:
        return self._get("Cisco-IOS-XE-native:native/version")["Cisco-IOS-XE-native:version"]

    def system_data(self) -> dict:
        path = (
            "Cisco-IOS-XE-device-hardware-oper:device-hardware-data/"
            "device-hardware/device-system-data"
        )
        try:
            data = self._get(path)
            return next(iter(data.values()), {}) or {}
        except DeviceError:
            return {}

    def info(self) -> dict:
        native = self.native_version()
        sysd = self.system_data()
        boot, now = _parse_ts(sysd.get("boot-time")), _parse_ts(sysd.get("current-time"))
        uptime = int((now - boot).total_seconds()) if boot and now else None
        match = re.search(r"Version\s+([\w.()]+)", sysd.get("software-version", ""))
        return {
            "hostname": self.hostname(),
            "version": match.group(1) if match else native,
            "version_native": native,
            "boot_time": sysd.get("boot-time"),
            "uptime_seconds": uptime,
            "protocol": "RESTCONF",
        }

    def interfaces(self, with_config: bool = True) -> list[dict]:
        state = self._get("ietf-interfaces:interfaces-state")
        rows_state = state["ietf-interfaces:interfaces-state"].get("interface", [])
        cfg_map: dict[str, dict] = {}
        if with_config:
            cfg = self._get("ietf-interfaces:interfaces")
            for item in cfg["ietf-interfaces:interfaces"].get("interface", []):
                cfg_map[item["name"]] = item
        return [_interface_row(s, cfg_map.get(s.get("name"))) for s in rows_state]

    # -- control (Req. 4 y 5) ------------------------------------------
    def set_enabled(self, name: str, enabled: bool) -> bool:
        """enabled=False equivale a 'shutdown'; True a 'no shutdown'."""
        path = f"ietf-interfaces:interfaces/interface={quote(name, safe='')}"
        body = {"ietf-interfaces:interface": {"name": name, "enabled": bool(enabled)}}
        self._request("PATCH", path, data=json.dumps(body))
        return True


# ----------------------------------------------------------------------
def _clean(obj):
    """Quita prefijos y atributos xmlns del resultado de xmltodict."""
    if isinstance(obj, dict):
        return {
            k.split(":")[-1]: _clean(v) for k, v in obj.items() if not k.startswith("@xmlns")
        }
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class CiscoNetconf:
    """NETCONF sobre SSH (puerto 830) con ncclient."""

    def __init__(self, host, username, password, port=830, timeout=20):
        self.host, self.username, self.password = host, username, password
        self.port, self.timeout = port, timeout

    def _connect(self):
        from ncclient import manager  # import perezoso: solo si se usa NETCONF

        try:
            return manager.connect(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                hostkey_verify=False,
                look_for_keys=False,
                allow_agent=False,
                timeout=self.timeout,
            )
        except Exception as exc:  # ncclient lanza varias excepciones distintas
            raise DeviceError(f"Cisco {self.host} no responde por NETCONF: {exc}") from exc

    @staticmethod
    def _parse(reply) -> dict:
        import xmltodict

        return _clean(xmltodict.parse(reply.xml))

    def info(self) -> dict:
        flt = (
            f'<native xmlns="{NS_NATIVE}"><hostname/><version/></native>'
        )
        with self._connect() as m:
            data = self._parse(m.get(filter=("subtree", flt)))
        native = data["rpc-reply"]["data"]["native"]
        return {
            "hostname": native.get("hostname"),
            "version": native.get("version"),
            "version_native": native.get("version"),
            "boot_time": None,
            "uptime_seconds": None,
            "protocol": "NETCONF",
        }

    def interfaces(self, with_config: bool = True) -> list[dict]:  # noqa: ARG002
        flt = f'<interfaces-state xmlns="{NS_IF}"/>'
        with self._connect() as m:
            data = self._parse(m.get(filter=("subtree", flt)))
        root = data["rpc-reply"]["data"].get("interfaces-state") or {}
        rows = []
        for item in _as_list(root.get("interface")):
            stats = item.get("statistics") or {}
            rows.append(
                {
                    "name": item.get("name"),
                    "admin_status": item.get("admin-status", "unknown"),
                    "oper_status": item.get("oper-status", "unknown"),
                    "speed_bps": _int(item.get("speed")),
                    "in_octets": _int(stats.get("in-octets")),
                    "out_octets": _int(stats.get("out-octets")),
                    "description": "",
                    "ip": "",
                }
            )
        return rows

    def set_enabled(self, name: str, enabled: bool) -> bool:
        cfg = (
            f'<config><interfaces xmlns="{NS_IF}"><interface>'
            f"<name>{name}</name><enabled>{'true' if enabled else 'false'}</enabled>"
            "</interface></interfaces></config>"
        )
        with self._connect() as m:
            m.edit_config(target="running", config=cfg)
        return True
