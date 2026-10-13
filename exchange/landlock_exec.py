"""Run one command under a Linux Landlock filesystem sandbox, then exec it.

    python3 -I exchange/landlock_exec.py --rw HOME [--rw P] [--ro P] [--sock DIR] -- CMD ARGS...

The Linux counterpart of the macOS sandbox-exec profiles: the process may
read and execute only beneath the --ro paths, read and write only beneath
the --rw paths, and create Unix sockets only beneath --sock paths. Every
other path, including every other wallet's home, is denied by the kernel.
The ruleset is inherited by the exec'd tool and cannot be lifted. If the
kernel lacks Landlock the command is refused, never run unconfined.
"""

from __future__ import annotations

import ctypes
import errno
import os
import struct
import sys

SYS_CREATE_RULESET, SYS_ADD_RULE, SYS_RESTRICT_SELF = 444, 445, 446  # same on x86_64 and aarch64
CREATE_RULESET_VERSION = 1
RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38

EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
REMOVE_DIR, REMOVE_FILE, MAKE_CHAR, MAKE_DIR = 1 << 4, 1 << 5, 1 << 6, 1 << 7
MAKE_REG, MAKE_SOCK, MAKE_FIFO, MAKE_BLOCK, MAKE_SYM = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12
REFER, TRUNCATE, IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
FILE_RIGHTS = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE | IOCTL_DEV
READ_ONLY = EXECUTE | READ_FILE | READ_DIR
READ_WRITE = (READ_ONLY | WRITE_FILE | REMOVE_DIR | REMOVE_FILE | MAKE_DIR | MAKE_REG
              | TRUNCATE)


class LandlockError(OSError):
    pass


def _syscall(number: int, *args) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    result = libc.syscall(number, *args)
    if result < 0:
        code = ctypes.get_errno()
        raise LandlockError(code, os.strerror(code))
    return result


def abi_version() -> int:
    try:
        return _syscall(SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(CREATE_RULESET_VERSION))
    except LandlockError as exc:
        if exc.errno in (errno.ENOSYS, errno.EOPNOTSUPP):
            return 0
        raise


def handled_rights(abi: int) -> int:
    rights = (1 << 13) - 1
    if abi >= 2:
        rights |= REFER
    if abi >= 3:
        rights |= TRUNCATE
    if abi >= 5:
        rights |= IOCTL_DEV
    return rights


def restrict(read_only: list[str], read_write: list[str], sockets: list[str]) -> int:
    abi = abi_version()
    if abi < 1:
        raise LandlockError(errno.EOPNOTSUPP, "Landlock is unavailable; refusing to run unconfined")
    handled = handled_rights(abi)
    attr = struct.pack("=Q", handled)
    ruleset = _syscall(SYS_CREATE_RULESET, ctypes.c_char_p(attr), ctypes.c_size_t(len(attr)), ctypes.c_uint32(0))
    try:
        grants = [(path, READ_ONLY) for path in read_only] + [(path, READ_WRITE) for path in read_write] + \
                 [(path, READ_WRITE | MAKE_SOCK) for path in sockets]
        for path, rights in grants:
            if not os.path.exists(path):
                continue
            descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                allowed = rights & handled
                if not os.path.isdir(path):
                    allowed &= FILE_RIGHTS
                rule = struct.pack("=Qi", allowed, descriptor)
                _syscall(SYS_ADD_RULE, ruleset, ctypes.c_uint32(RULE_PATH_BENEATH), ctypes.c_char_p(rule),
                         ctypes.c_uint32(0))
            finally:
                os.close(descriptor)
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise LandlockError(ctypes.get_errno(), "no_new_privs failed")
        _syscall(SYS_RESTRICT_SELF, ruleset, ctypes.c_uint32(0))
    finally:
        os.close(ruleset)
    return abi


def main(argv: list[str]) -> None:
    if "--" not in argv:
        raise SystemExit("usage: landlock_exec [--ro P] [--rw P] [--sock P] -- CMD ...")
    split = argv.index("--")
    options, command = argv[:split], argv[split + 1:]
    groups: dict[str, list[str]] = {"--ro": [], "--rw": [], "--sock": []}
    if len(options) % 2 or not command:
        raise SystemExit("invalid arguments")
    for flag, value in zip(options[::2], options[1::2]):
        if flag not in groups or not os.path.isabs(value):
            raise SystemExit(f"invalid option {flag}")
        groups[flag].append(value)
    try:
        restrict(groups["--ro"], groups["--rw"], groups["--sock"])
    except LandlockError as exc:
        print(f"PHASE35F_SIGNER_REJECTED role=Sandbox reason=landlock:{exc.strerror}", flush=True)
        raise SystemExit(126)
    os.execv(command[0], command)


if __name__ == "__main__":
    main(sys.argv[1:])
