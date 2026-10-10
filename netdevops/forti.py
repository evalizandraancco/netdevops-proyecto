"""Cliente de la API REST de FortiOS (Req. 1)."""
from __future__ import annotations

import ipaddress

import requests
import urllib3

from .errors import DeviceError


def prefix_to_netmask(mask) -> str:
    """Acepta 24, '24' o '255.255.255.0' y devuelve la máscara con puntos."""
    text = str(mask)
    if text.isdigit():
        return str(ipaddress.IPv4Network(f"0.0.0.0/{text}").netmask)
    return text


class FortiClient:
    def __init__(self, host, api_key, interface="port2", verify=False, timeout=10):
        self.host = host
        self.default_interface = interface
        self.timeout = timeout
        self.base = f"https://{host}/api/v2"
        self.session = requests.Session()
        self.session.verify = verify
        self.session.trust_env = False  # ignora proxies/CA del entorno: el laboratorio es local
        self.session.headers["Authorization"] = f"Bearer {api_key}"
        if not verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base}/{path}"
        try:
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise DeviceError(f"FortiGate {self.host} no responde: {exc}") from exc
        if resp.status_code in (401, 403):
            raise DeviceError(
                f"FortiGate rechazó la API Key/trusthost (HTTP {resp.status_code}). "
                "Revisa la clave y que tu IP esté en el trusthost del api-user."
            )
        if resp.status_code >= 400:
            raise DeviceError(f"FortiGate {method} {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise DeviceError(f"Respuesta no JSON de {path}") from exc

    # -- información general (Req. 1) ----------------------------------
    def status(self) -> dict:
        data = self._request("GET", "monitor/system/status")
        res = data.get("results", {}) or {}
        return {
            "hostname": res.get("hostname"),
            "model": res.get("model_name") or res.get("model"),
            "version": data.get("version"),
            "build": data.get("build"),
            "serial": data.get("serial"),
        }

    def interface_admin_status(self, name: str | None = None) -> str | None:
        name = name or self.default_interface
        try:
            data = self._request("GET", f"cmdb/system/interface/{name}")
        except DeviceError:
            return None
        results = data.get("results")
        if isinstance(results, list) and results:
            return results[0].get("status")
        if isinstance(results, dict):
            return results.get("status")
        return None

    def interface(self, name: str | None = None) -> dict:
        """IP, máscara, enlace, velocidad y contadores de la interfaz (wan1)."""
        name = name or self.default_interface
        data = self._request("GET", "monitor/system/interface", params={"interface_name": name})
        results = data.get("results", {}) or {}
        if isinstance(results, dict):
            item = results.get(name)
        else:  # algunas versiones devuelven una lista de interfaces
            item = next((r for r in results if r.get("name") == name or r.get("id") == name), None)
        if not item:
            raise DeviceError(f"La interfaz {name} no aparece en la respuesta de FortiGate")
        link = bool(item.get("link"))
        admin = self.interface_admin_status(name)
        speed_mbps = item.get("speed") or 0
        return {
            "name": name,
            "alias": item.get("alias", ""),
            "ip": item.get("ip", ""),
            "mask": prefix_to_netmask(item.get("mask", "")),
            "link": link,
            "admin_status": admin or ("up" if link else "down"),
            "speed_bps": int(float(speed_mbps) * 1_000_000),
            "tx_bytes": int(item.get("tx_bytes", 0)),
            "rx_bytes": int(item.get("rx_bytes", 0)),
        }

    # -- control (Req. 4 y 5) ------------------------------------------
    def set_enabled(self, name: str, enabled: bool) -> bool:
        """enabled=False -> status down (shutdown); True -> status up."""
        self._request(
            "PUT",
            f"cmdb/system/interface/{name}",
            json={"status": "up" if enabled else "down"},
        )
        return True
