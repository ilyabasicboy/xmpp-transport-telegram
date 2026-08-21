import os
import signal
import sys
from typing import Optional


def read_pid(pid_file: str) -> Optional[int]:
    try:
        with open(pid_file, "r", encoding="ascii") as handle:
            return int(handle.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_pid(pid_file: str) -> None:
    directory = os.path.dirname(pid_file)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(pid_file, "w", encoding="ascii") as handle:
        handle.write(str(os.getpid()))


def remove_pid(pid_file: str) -> None:
    try:
        os.unlink(pid_file)
    except FileNotFoundError:
        pass


def stop(pid_file: str) -> bool:
    pid = read_pid(pid_file)
    if pid is None:
        return False
    os.kill(pid, signal.SIGTERM)
    return True


def daemonize(pid_file: str) -> None:
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    sys.stdin.close()
    write_pid(pid_file)
