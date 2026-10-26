"""Policy tests on synthetic ABI records; NOT proof of native macOS execution."""
import ctypes
import pytest
from newsmap_sidecar.platform_storage import StatFS64, validate_darwin_volume
from newsmap_sidecar.common import NewsmapError


def test_darwin_abi_layout_and_local_apfs_policy():
    assert ctypes.sizeof(StatFS64) == 2168
    assert StatFS64.flags.offset == 64 and StatFS64.fstype.offset == 72
    info = StatFS64(); info.flags = 0x1000; info.fstype = b'apfs'; info.device = b'/dev/disk3s5'
    assert validate_darwin_volume(info) == 'apfs'
    for flags, kind, device in [(0, b'apfs', b'/dev/disk1'), (0x1001, b'apfs', b'/dev/disk1'),
                                (0x1020, b'apfs', b'/dev/disk1'), (0x1000, b'nfs', b'server'),
                                (0x1000, b'tmpfs', b'RAM'), (0x1000,b'apfs',b'unknown')]:
        info.flags, info.fstype, info.device = flags, kind, device
        with pytest.raises(NewsmapError): validate_darwin_volume(info)
