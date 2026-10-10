"""Telemetría por polling y cálculo de ocupación de ancho de banda (Req. 2).

Ocupación (%) = ((ΔTx + ΔRx) * 8) / (Δt * capacidad_bps) * 100
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .errors import DeviceError


def occupancy_percent(delta_tx: int, delta_rx: int, delta_t: float, capacity_bps: float) -> float | None:
    """Aplica la fórmula del proyecto. Devuelve None si los datos no son válidos."""
    if delta_t <= 0 or capacity_bps <= 0 or delta_tx < 0 or delta_rx < 0:
        return None
    return ((delta_tx + delta_rx) * 8) / (delta_t * capacity_bps) * 100


@dataclass
class Sample:
    device: str
    interface: str
    admin_up: bool
    oper_up: bool
    capacity_bps: int
    occupancy: float | None = None
    tx_bps: float | None = None
    rx_bps: float | None = None
    description: str = ""
    ip: str = ""

    @property
    def key(self) -> str:
        return f"{self.device}:{self.interface}"


class RateCalculator:
    """Guarda la lectura anterior de los contadores y calcula los deltas."""

    def __init__(self):
        self._prev: dict[str, tuple[int, int, float]] = {}

    def update(self, key: str, tx: int, rx: int, t: float):
        """Devuelve (Δtx, Δrx, Δt) o None en la primera lectura / reinicio de contadores."""
        prev = self._prev.get(key)
        self._prev[key] = (tx, rx, t)
        if prev is None:
            return None
        d_tx, d_rx, d_t = tx - prev[0], rx - prev[1], t - prev[2]
        if d_tx < 0 or d_rx < 0 or d_t <= 0:  # contadores reiniciados
            return None
        return d_tx, d_rx, d_t

    def reset(self, key: str) -> None:
        self._prev.pop(key, None)


class TelemetryCollector:
    """Sondea el switch Cisco (todas las interfaces activas) y wan1 del FortiGate."""

    def __init__(self, cfg: dict, cisco, forti, clock=time.monotonic):
        self.cfg, self.cisco, self.forti, self.clock = cfg, cisco, forti, clock
        self.rates = RateCalculator()
        self.errors: dict[str, str] = {}
        self._cisco_meta: dict[str, dict] = {}

    def _capacity(self, override, reported: int) -> int:
        return int(override or reported or self.cfg["monitor"]["default_capacity_bps"])

    def poll(self) -> list[Sample]:
        self.errors = {}
        samples: list[Sample] = []
        pollers = [("cisco", self._poll_cisco)]
        if self.cfg.get("forti", {}).get("enabled", True):
            pollers.append(("forti", self._poll_forti))
        for name, fn in pollers:
            try:
                samples.extend(fn())
            except DeviceError as exc:
                self.errors[name] = str(exc)
        return samples

    # ------------------------------------------------------------------
    def _poll_cisco(self) -> list[Sample]:
        rows = self.cisco.interfaces(with_config=not self._cisco_meta)
        t = self.clock()
        overrides = self.cfg["cisco"].get("capacity_override_bps") or {}
        out = []
        for row in rows:
            name = row["name"]
            if row["description"] or row["ip"]:
                self._cisco_meta[name] = {"description": row["description"], "ip": row["ip"]}
            meta = self._cisco_meta.get(name, {})
            admin_up = row["admin_status"] == "up"
            oper_up = row["oper_status"] == "up"
            capacity = self._capacity(overrides.get(name), row["speed_bps"])
            sample = Sample("cisco", name, admin_up, oper_up, capacity,
                            description=meta.get("description", ""), ip=meta.get("ip", ""))
            self._fill_rates(sample, row["out_octets"], row["in_octets"], t)
            out.append(sample)
        return out

    def _poll_forti(self) -> list[Sample]:
        iface = self.forti.interface()
        t = self.clock()
        name = iface["name"]
        capacity = self._capacity(self.cfg["forti"].get("capacity_override_bps"), iface["speed_bps"])
        sample = Sample("forti", name, iface["admin_status"] == "up", iface["link"], capacity,
                        description=iface.get("alias", ""), ip=iface.get("ip", ""))
        self._fill_rates(sample, iface["tx_bytes"], iface["rx_bytes"], t)
        return [sample]

    def _fill_rates(self, sample: Sample, tx: int, rx: int, t: float) -> None:
        if not (sample.admin_up and sample.oper_up):
            self.rates.reset(sample.key)  # no medimos interfaces caídas
            return
        delta = self.rates.update(sample.key, tx, rx, t)
        if delta is None:
            return
        d_tx, d_rx, d_t = delta
        sample.tx_bps = d_tx * 8 / d_t
        sample.rx_bps = d_rx * 8 / d_t
        sample.occupancy = occupancy_percent(d_tx, d_rx, d_t, sample.capacity_bps)
