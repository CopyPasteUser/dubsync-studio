"""Helpers for local and mounted network media sources (SMB/CIFS/NFS/GVFS)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

NETWORK_FILESYSTEMS = {
    "cifs", "smb3", "nfs", "nfs4", "sshfs", "fuse.sshfs", "davfs",
    "fuse.gvfsd-fuse", "fuse.rclone", "fuse.s3fs", "ceph", "9p",
}


def _unescape_mount_field(value: str) -> str:
    """Decode octal escapes used in /proc/*/mounts without shell evaluation."""
    return (value.replace("\\040", " ").replace("\\011", "\t")
            .replace("\\012", "\n").replace("\\134", "\\"))


def parse_mount_table(text: str) -> list[dict[str, str]]:
    """Parse Linux mount-table rows, preserving escaped spaces and unusual paths."""
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        source, mountpoint, fs_type = fields[0], _unescape_mount_field(fields[1]), fields[2]
        if fs_type.lower() in NETWORK_FILESYSTEMS:
            rows.append({"source": _unescape_mount_field(source),
                         "mountpoint": mountpoint, "filesystem": fs_type.lower()})
    return rows


def network_mounts(mount_table: str | os.PathLike[str] = "/proc/self/mounts") -> list[dict[str, str]]:
    try:
        text = Path(mount_table).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return parse_mount_table(text)


def network_mount_for_path(path: str | os.PathLike[str], mounts: Iterable[dict[str, str]] | None = None) -> dict[str, str] | None:
    """Return the deepest network mount containing a path, if any."""
    target = os.path.abspath(os.path.expanduser(os.fspath(path)))
    candidates = list(mounts) if mounts is not None else network_mounts()
    best: dict[str, str] | None = None
    best_len = -1
    for row in candidates:
        mountpoint = os.path.abspath(row.get("mountpoint", "/"))
        try:
            if os.path.commonpath((target, mountpoint)) != mountpoint:
                continue
        except (ValueError, OSError):
            continue
        if len(mountpoint) > best_len:
            best, best_len = row, len(mountpoint)
    return dict(best) if best else None


def is_network_path(path: str | os.PathLike[str]) -> bool:
    return network_mount_for_path(path) is not None


def network_mount_type_label(filesystem: str) -> str:
    labels = {
        "cifs": "SMB/CIFS", "smb3": "SMB", "nfs": "NFS", "nfs4": "NFS",
        "sshfs": "SSHFS", "fuse.sshfs": "SSHFS", "davfs": "WebDAV",
        "fuse.gvfsd-fuse": "GVFS", "fuse.rclone": "rclone", "fuse.s3fs": "S3/FUSE",
        "ceph": "Ceph", "9p": "9p-Netzwerk",
    }
    value = str(filesystem or "").lower()
    return labels.get(value, value.upper() or "Netzwerk")


def network_mount_candidates() -> list[Path]:
    """Collect mounted network roots plus common removable/mount directories."""
    result: list[Path] = []
    for row in network_mounts():
        # Do not stat a network mount while merely constructing the picker list:
        # a disconnected hard-mounted NFS share can block metadata requests.
        path = Path(row["mountpoint"])
        if path not in result:
            result.append(path)
    uid = os.getuid() if hasattr(os, "getuid") else None
    if uid is not None:
        gvfs = Path(f"/run/user/{uid}/gvfs")
        if gvfs not in result:
            result.append(gvfs)
    user = os.environ.get("USER") or Path.home().name
    for raw in ("/mnt", f"/media/{user}", f"/run/media/{user}"):
        path = Path(raw)
        if path.is_dir() and path not in result:
            result.append(path)
    return result
