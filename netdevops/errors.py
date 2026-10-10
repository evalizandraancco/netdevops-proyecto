"""Excepciones comunes."""


class DeviceError(RuntimeError):
    """Fallo al hablar con un equipo (red, autenticación o respuesta inesperada)."""
