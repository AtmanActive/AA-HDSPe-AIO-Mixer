"""Minimal ctypes binding to libasound's control API (snd_ctl_*), enough for this app.

Elements are addressed by (iface, name, index). Every call raises AlsaError on failure, with the errno.
"""
from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass

IFACE_CARD = 0
IFACE_HWDEP = 1
IFACE_MIXER = 2
IFACE_NAMES = {"CARD": IFACE_CARD, "HWDEP": IFACE_HWDEP, "MIXER": IFACE_MIXER}

TYPE_BOOLEAN = 1
TYPE_INTEGER = 2
TYPE_ENUMERATED = 3

_p = ctypes.c_void_p
_pp = ctypes.POINTER(ctypes.c_void_p)
_lib = None


def _load():
    global _lib
    if _lib is not None:
        return _lib
    lib = ctypes.CDLL(ctypes.util.find_library("asound") or "libasound.so.2")
    sig = {
        "snd_strerror": (ctypes.c_char_p, [ctypes.c_int]),
        "snd_ctl_open": (ctypes.c_int, [_pp, ctypes.c_char_p, ctypes.c_int]),
        "snd_ctl_close": (ctypes.c_int, [_p]),
        "snd_ctl_card_info_malloc": (ctypes.c_int, [_pp]),
        "snd_ctl_card_info_free": (None, [_p]),
        "snd_ctl_card_info": (ctypes.c_int, [_p, _p]),
        "snd_ctl_card_info_get_id": (ctypes.c_char_p, [_p]),
        "snd_ctl_card_info_get_driver": (ctypes.c_char_p, [_p]),
        "snd_ctl_card_info_get_name": (ctypes.c_char_p, [_p]),
        "snd_ctl_card_info_get_longname": (ctypes.c_char_p, [_p]),
        "snd_ctl_elem_list_malloc": (ctypes.c_int, [_pp]),
        "snd_ctl_elem_list_free": (None, [_p]),
        "snd_ctl_elem_list": (ctypes.c_int, [_p, _p]),
        "snd_ctl_elem_list_get_count": (ctypes.c_uint, [_p]),
        "snd_ctl_elem_list_get_used": (ctypes.c_uint, [_p]),
        "snd_ctl_elem_list_alloc_space": (ctypes.c_int, [_p, ctypes.c_uint]),
        "snd_ctl_elem_list_free_space": (None, [_p]),
        "snd_ctl_elem_list_get_interface": (ctypes.c_int, [_p, ctypes.c_uint]),
        "snd_ctl_elem_list_get_name": (ctypes.c_char_p, [_p, ctypes.c_uint]),
        "snd_ctl_elem_list_get_index": (ctypes.c_uint, [_p, ctypes.c_uint]),
        "snd_ctl_elem_info_malloc": (ctypes.c_int, [_pp]),
        "snd_ctl_elem_info_free": (None, [_p]),
        "snd_ctl_elem_info_clear": (None, [_p]),
        "snd_ctl_elem_value_clear": (None, [_p]),
        "snd_ctl_elem_info_set_interface": (None, [_p, ctypes.c_int]),
        "snd_ctl_elem_info_set_name": (None, [_p, ctypes.c_char_p]),
        "snd_ctl_elem_info_set_index": (None, [_p, ctypes.c_uint]),
        "snd_ctl_elem_info_set_item": (None, [_p, ctypes.c_uint]),
        "snd_ctl_elem_info": (ctypes.c_int, [_p, _p]),
        "snd_ctl_elem_info_get_type": (ctypes.c_int, [_p]),
        "snd_ctl_elem_info_get_count": (ctypes.c_uint, [_p]),
        "snd_ctl_elem_info_get_items": (ctypes.c_uint, [_p]),
        "snd_ctl_elem_info_get_item_name": (ctypes.c_char_p, [_p]),
        "snd_ctl_elem_info_get_min": (ctypes.c_long, [_p]),
        "snd_ctl_elem_info_get_max": (ctypes.c_long, [_p]),
        "snd_ctl_elem_value_malloc": (ctypes.c_int, [_pp]),
        "snd_ctl_elem_value_free": (None, [_p]),
        "snd_ctl_elem_value_set_interface": (None, [_p, ctypes.c_int]),
        "snd_ctl_elem_value_set_name": (None, [_p, ctypes.c_char_p]),
        "snd_ctl_elem_value_set_index": (None, [_p, ctypes.c_uint]),
        "snd_ctl_elem_value_set_integer": (None, [_p, ctypes.c_uint, ctypes.c_long]),
        "snd_ctl_elem_value_get_integer": (ctypes.c_long, [_p, ctypes.c_uint]),
        "snd_ctl_elem_value_set_boolean": (None, [_p, ctypes.c_uint, ctypes.c_long]),
        "snd_ctl_elem_value_get_boolean": (ctypes.c_int, [_p, ctypes.c_uint]),
        "snd_ctl_elem_value_set_enumerated": (None, [_p, ctypes.c_uint, ctypes.c_uint]),
        "snd_ctl_elem_value_get_enumerated": (ctypes.c_uint, [_p, ctypes.c_uint]),
        "snd_ctl_elem_read": (ctypes.c_int, [_p, _p]),
        "snd_ctl_elem_write": (ctypes.c_int, [_p, _p]),
    }
    for name, (res, args) in sig.items():
        f = getattr(lib, name)
        f.restype, f.argtypes = res, args
    _lib = lib
    return lib


class AlsaError(OSError):
    pass


def _check(err: int, what: str) -> int:
    if err < 0:
        raise AlsaError(-err, f"{what}: {_load().snd_strerror(err).decode()}")
    return err


@dataclass(frozen=True)
class ElemId:
    iface: int
    name: str
    index: int = 0


@dataclass
class ElemInfo:
    type: int
    count: int
    min: int = 0
    max: int = 0
    items: tuple[str, ...] = ()


class Ctl:
    """An open control device (hw:N)."""

    def __init__(self, card: int):
        lib = _load()
        self.card = card
        self._h = ctypes.c_void_p()
        _check(lib.snd_ctl_open(ctypes.byref(self._h), f"hw:{card}".encode(), 0), f"open hw:{card}")
        self._val = ctypes.c_void_p()
        _check(lib.snd_ctl_elem_value_malloc(ctypes.byref(self._val)), "elem_value_malloc")
        self._info = ctypes.c_void_p()
        _check(lib.snd_ctl_elem_info_malloc(ctypes.byref(self._info)), "elem_info_malloc")
        # Dedicated, pre-addressed value for the hot path (Mixer cell writes).
        self._mixval = ctypes.c_void_p()
        _check(lib.snd_ctl_elem_value_malloc(ctypes.byref(self._mixval)), "elem_value_malloc")
        lib.snd_ctl_elem_value_set_interface(self._mixval, IFACE_HWDEP)
        lib.snd_ctl_elem_value_set_name(self._mixval, b"Mixer")

    def close(self):
        lib = _load()
        if self._h:
            lib.snd_ctl_elem_value_free(self._mixval)
            lib.snd_ctl_elem_value_free(self._val)
            lib.snd_ctl_elem_info_free(self._info)
            lib.snd_ctl_close(self._h)
            self._h = ctypes.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- card ---------------------------------------------------------------------------------
    def card_info(self) -> dict:
        lib = _load()
        ci = ctypes.c_void_p()
        _check(lib.snd_ctl_card_info_malloc(ctypes.byref(ci)), "card_info_malloc")
        try:
            _check(lib.snd_ctl_card_info(self._h, ci), "card_info")
            g = lambda f: (f(ci) or b"").decode(errors="replace")
            return {"id": g(lib.snd_ctl_card_info_get_id), "driver": g(lib.snd_ctl_card_info_get_driver),
                    "name": g(lib.snd_ctl_card_info_get_name), "longname": g(lib.snd_ctl_card_info_get_longname)}
        finally:
            lib.snd_ctl_card_info_free(ci)

    def list_elements(self) -> list[ElemId]:
        lib = _load()
        lst = ctypes.c_void_p()
        _check(lib.snd_ctl_elem_list_malloc(ctypes.byref(lst)), "elem_list_malloc")
        try:
            _check(lib.snd_ctl_elem_list(self._h, lst), "elem_list")
            n = lib.snd_ctl_elem_list_get_count(lst)
            _check(lib.snd_ctl_elem_list_alloc_space(lst, n), "elem_list_alloc_space")
            _check(lib.snd_ctl_elem_list(self._h, lst), "elem_list")
            out = [ElemId(lib.snd_ctl_elem_list_get_interface(lst, i),
                          lib.snd_ctl_elem_list_get_name(lst, i).decode(),
                          lib.snd_ctl_elem_list_get_index(lst, i))
                   for i in range(lib.snd_ctl_elem_list_get_used(lst))]
            lib.snd_ctl_elem_list_free_space(lst)
            return out
        finally:
            lib.snd_ctl_elem_list_free(lst)

    # --- generic elements ---------------------------------------------------------------------
    def _address(self, setter_if, setter_name, setter_idx, obj, eid: ElemId):
        # Clear first: after a read/write the kernel fills in the numid, which would otherwise win
        # over the name we set next and silently address the previous element.
        lib = _load()
        (lib.snd_ctl_elem_info_clear if obj is self._info else lib.snd_ctl_elem_value_clear)(obj)
        setter_if(obj, eid.iface)
        setter_name(obj, eid.name.encode())
        setter_idx(obj, eid.index)

    def info(self, eid: ElemId) -> ElemInfo:
        lib = _load()
        self._address(lib.snd_ctl_elem_info_set_interface, lib.snd_ctl_elem_info_set_name,
                      lib.snd_ctl_elem_info_set_index, self._info, eid)
        _check(lib.snd_ctl_elem_info(self._h, self._info), f"info {eid.name}")
        t = lib.snd_ctl_elem_info_get_type(self._info)
        inf = ElemInfo(type=t, count=lib.snd_ctl_elem_info_get_count(self._info))
        if t == TYPE_INTEGER:
            inf.min = lib.snd_ctl_elem_info_get_min(self._info)
            inf.max = lib.snd_ctl_elem_info_get_max(self._info)
        elif t == TYPE_ENUMERATED:
            names = []
            for i in range(lib.snd_ctl_elem_info_get_items(self._info)):
                lib.snd_ctl_elem_info_set_item(self._info, i)
                _check(lib.snd_ctl_elem_info(self._h, self._info), f"info {eid.name} item {i}")
                names.append(lib.snd_ctl_elem_info_get_item_name(self._info).decode(errors="replace"))
            inf.items = tuple(names)
        return inf

    def read(self, eid: ElemId, type_: int | None = None, count: int = 1) -> list[int]:
        lib = _load()
        if type_ is None:
            inf = self.info(eid)
            type_, count = inf.type, inf.count
        self._address(lib.snd_ctl_elem_value_set_interface, lib.snd_ctl_elem_value_set_name,
                      lib.snd_ctl_elem_value_set_index, self._val, eid)
        _check(lib.snd_ctl_elem_read(self._h, self._val), f"read {eid.name}")
        get = {TYPE_BOOLEAN: lib.snd_ctl_elem_value_get_boolean,
               TYPE_ENUMERATED: lib.snd_ctl_elem_value_get_enumerated}.get(type_, lib.snd_ctl_elem_value_get_integer)
        return [int(get(self._val, i)) for i in range(count)]

    def write(self, eid: ElemId, values: list[int], type_: int | None = None):
        lib = _load()
        if type_ is None:
            type_ = self.info(eid).type
        self._address(lib.snd_ctl_elem_value_set_interface, lib.snd_ctl_elem_value_set_name,
                      lib.snd_ctl_elem_value_set_index, self._val, eid)
        put = {TYPE_BOOLEAN: lib.snd_ctl_elem_value_set_boolean,
               TYPE_ENUMERATED: lib.snd_ctl_elem_value_set_enumerated}.get(type_, lib.snd_ctl_elem_value_set_integer)
        for i, v in enumerate(values):
            put(self._val, i, int(v))
        _check(lib.snd_ctl_elem_write(self._h, self._val), f"write {eid.name}")

    def read_enum_name(self, eid: ElemId) -> str:
        inf = self.info(eid)
        idx = self.read(eid, inf.type, 1)[0]
        return inf.items[idx] if 0 <= idx < len(inf.items) else str(idx)

    # --- hot path -----------------------------------------------------------------------------
    def write_mixer_cell(self, src: int, dst: int, gain: int):
        """Write one cell of the hardware matrix through the HWDEP 'Mixer' control (both drivers)."""
        lib = _load()
        v = self._mixval
        lib.snd_ctl_elem_value_set_integer(v, 0, src)
        lib.snd_ctl_elem_value_set_integer(v, 1, dst)
        lib.snd_ctl_elem_value_set_integer(v, 2, gain)
        _check(lib.snd_ctl_elem_write(self._h, v), f"Mixer write src={src} dst={dst}")
