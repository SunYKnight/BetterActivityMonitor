"""Interface addresses via libc getifaddrs(); the standard library exposes no equivalent."""

import ctypes
import ctypes.util
import ipaddress
import socket


class _SockAddr(ctypes.Structure):
    _fields_ = [("sa_family", ctypes.c_ushort), ("sa_data", ctypes.c_ubyte * 14)]


class _SockAddrIn(ctypes.Structure):
    _fields_ = [
        ("sin_family", ctypes.c_ushort), ("sin_port", ctypes.c_uint16), ("sin_addr", ctypes.c_ubyte * 4),
        ("sin_zero", ctypes.c_ubyte * 8),
    ]


class _SockAddrIn6(ctypes.Structure):
    _fields_ = [
        ("sin6_family", ctypes.c_ushort), ("sin6_port", ctypes.c_uint16), ("sin6_flowinfo", ctypes.c_uint32),
        ("sin6_addr", ctypes.c_ubyte * 16), ("sin6_scope_id", ctypes.c_uint32),
    ]


class _IfAddrs(ctypes.Structure):
    pass


_IfAddrs._fields_ = [
    ("ifa_next", ctypes.POINTER(_IfAddrs)), ("ifa_name", ctypes.c_char_p), ("ifa_flags", ctypes.c_uint),
    ("ifa_addr", ctypes.POINTER(_SockAddr)), ("ifa_netmask", ctypes.POINTER(_SockAddr)),
    ("ifa_ifu", ctypes.POINTER(_SockAddr)), ("ifa_data", ctypes.c_void_p),
]

_libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
_libc.getifaddrs.argtypes = [ctypes.POINTER(ctypes.POINTER(_IfAddrs))]
_libc.freeifaddrs.argtypes = [ctypes.POINTER(_IfAddrs)]


def _address(pointer) -> str | None:
    family = pointer.contents.sa_family if pointer else None
    result = None
    if family == socket.AF_INET:
        result = str(ipaddress.IPv4Address(bytes(ctypes.cast(pointer, ctypes.POINTER(_SockAddrIn)).contents.sin_addr)))
    elif family == socket.AF_INET6:
        raw = bytes(ctypes.cast(pointer, ctypes.POINTER(_SockAddrIn6)).contents.sin6_addr)
        result = str(ipaddress.IPv6Address(raw))
    return result


def interface_addresses() -> dict[str, list[str]]:
    """Interface name → ["192.168.1.2/24", "fe80::1/64", …], IPv4 first."""
    head = ctypes.POINTER(_IfAddrs)()
    if _libc.getifaddrs(ctypes.byref(head)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, "getifaddrs failed")
    result: dict[str, list[str]] = {}
    try:
        node = head
        while node:
            entry = node.contents
            address = _address(entry.ifa_addr)
            mask = _address(entry.ifa_netmask)
            if address and mask:
                prefix = bin(int(ipaddress.ip_address(mask))).count("1")
                result.setdefault(entry.ifa_name.decode(), []).append(f"{address}/{prefix}")
            node = entry.ifa_next
    finally:
        _libc.freeifaddrs(head)
    for addresses in result.values():
        addresses.sort(key=lambda text: ":" in text)
    return result
