"""Darwin local-volume verification using the 64-bit statfs ABI, stdlib only.

ABI reference: Apple xnu bsd/sys/mount.h (__DARWIN_STRUCT_STATFS64).
Policy: only local APFS, not network/union/read-only volumes. No Linux-guard bypass.
Actual macOS durability acceptance still requires a native test run.
"""
import ctypes as ct
import os
import platform

from .common import require


class StatFS64(ct.Structure):
    _fields_ = [('block_size', ct.c_uint32), ('io_size', ct.c_int32),
                ('blocks', ct.c_uint64), ('free', ct.c_uint64), ('available', ct.c_uint64),
                ('files', ct.c_uint64), ('files_free', ct.c_uint64), ('fsid', ct.c_int32 * 2),
                ('owner', ct.c_uint32), ('type', ct.c_uint32), ('flags', ct.c_uint32),
                ('subtype', ct.c_uint32), ('fstype', ct.c_char * 16),
                ('mountpoint', ct.c_char * 1024), ('device', ct.c_char * 1024),
                ('reserved', ct.c_uint32 * 8)]


def validate_darwin_volume(info):
    require(bool(info.flags & 0x1000), 'macOS volume is not local')  # MNT_LOCAL
    require(not info.flags & (0x1 | 0x20), 'read-only or union volume not supported')
    kind = info.fstype.decode('ascii', errors='strict')
    require(kind == 'apfs', 'macOS storage requires a local APFS volume')
    require(info.device.startswith(b'/dev/'), 'macOS persistent block-device mount not verified')
    return kind


def darwin_mount_type(path):
    require(platform.system() == 'Darwin', 'Darwin mount check requires macOS')
    require(ct.sizeof(StatFS64) == 2168 and ct.sizeof(ct.c_void_p) == 8, 'unsupported Darwin statfs ABI')
    existing = path
    while not existing.exists(): existing = existing.parent
    libc = ct.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    symbol = 'statfs$INODE64' if platform.machine() == 'x86_64' else 'statfs'
    call = getattr(libc, symbol, None)
    require(call is not None, 'Darwin statfs64 entry point unavailable')
    call.argtypes = [ct.c_char_p, ct.POINTER(StatFS64)]
    call.restype = ct.c_int
    info = StatFS64()
    require(call(os.fsencode(existing), ct.byref(info)) == 0, 'Darwin volume inspection failed')
    return validate_darwin_volume(info)
