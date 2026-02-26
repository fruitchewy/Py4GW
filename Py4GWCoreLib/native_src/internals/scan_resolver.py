"""Thin logging wrapper for scan resolution.

Usage:
    addr = resolve_scan("SkipCinematic",
        lambda: Scanner.ToFunctionStart(
            Scanner.FindAssertion("CiCliApi.cpp", "context->script", 0, 0), 0xFFF))
"""

from typing import Callable
from ...Scanner import Scanner, ScannerSection
import Py4GW

_log_entries: list[str] = []


def _log(msg: str) -> None:
    _log_entries.append(msg)
    Py4GW.Console.Log("ScanResolver", msg, Py4GW.Console.MessageType.Info)


def resolve_scan(
    name: str,
    resolver: Callable[[], int],
    validate_text_section: bool = True,
) -> int:
    """Resolve a scan address, log the result, optionally validate .text section."""
    try:
        addr = resolver()
    except Exception as e:
        _log(f"{name}: exception: {e}")
        return 0
    if not addr:
        _log(f"{name}: not found")
        return 0
    if validate_text_section and not Scanner.IsValidPtr(addr, ScannerSection.TEXT):
        _log(f"{name}: {hex(addr)} failed .text validation")
        return 0
    _log(f"{name}: {hex(addr)}")
    return addr


def resolve_symbol(name: str, resolver: Callable[[], int]) -> int:
    """Like resolve_scan but skips .text validation (for data addresses)."""
    return resolve_scan(name, resolver, validate_text_section=False)


def log_cpp_scans() -> None:
    """Log all C++ GWCA scan results and hook statuses."""
    status = Scanner.GetScanStatus()
    for name, addr in sorted(status.get("scans", {}).items()):
        if addr:
            _log(f"{name}: cpp -> {hex(addr)}")
        else:
            _log(f"{name}: cpp -> not found")
    for name, mh_status in sorted(status.get("hooks", {}).items()):
        if mh_status == 0:
            _log(f"{name}: cpp:hook -> ok")
        else:
            _log(f"{name}: cpp:hook -> MH_STATUS={mh_status}")


def get_resolution_log() -> list[str]:
    """Return all resolution log entries (for diagnostics)."""
    return list(_log_entries)


# C++ scans are already resolved before Python starts — log them now.
log_cpp_scans()
