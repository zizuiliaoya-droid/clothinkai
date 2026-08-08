"""Local-command collector adapter."""

from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from threading import Event

from worker.adapters.base import AdapterError, CollectorAdapter
from worker.models import Credential, TaskAssignment

_COMMAND_TIMEOUT = 1800.0
_CREATE_SUSPENDED = 0x00000004
_SAFE_ENV_KEYS = (
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "HOME",
    "LANG",
    "LC_ALL",
)

log = logging.getLogger(__name__)


def _collector_env() -> dict[str, str]:
    """只继承运行采集器所需的非敏感宿主环境。"""
    return {
        key: value
        for key in _SAFE_ENV_KEYS
        if (value := os.environ.get(key)) is not None
    }


class _WindowsJob:
    """Windows Job Object；关闭句柄时终止根进程及全部后代。"""

    def __init__(self, handle: int) -> None:
        self._handle = handle

    @classmethod
    def attach(cls, process: subprocess.Popen[bytes]) -> "_WindowsJob | None":
        if os.name != "nt":
            return None
        import ctypes
        from ctypes import wintypes

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        configured = kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        )
        process_handle = getattr(process, "_handle", None)
        assigned = bool(
            configured
            and process_handle is not None
            and kernel32.AssignProcessToJobObject(
                handle, wintypes.HANDLE(process_handle)
            )
        )
        if not assigned:
            kernel32.CloseHandle(handle)
            log.warning("collector_job_object_unavailable")
            return None
        return cls(int(handle))

    def close(self) -> None:
        if not self._handle:
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(wintypes.HANDLE(self._handle))
        self._handle = 0


def _resume_windows_process(process: subprocess.Popen[bytes]) -> None:
    """在已加入 Job Object 后恢复 CREATE_SUSPENDED 创建的进程。"""
    import ctypes
    from ctypes import wintypes

    process_handle = getattr(process, "_handle", None)
    if process_handle is None:
        raise OSError("collector process handle unavailable")
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = ntdll.NtResumeProcess(wintypes.HANDLE(process_handle))
    if status != 0:
        raise OSError("collector process could not be resumed")


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """终止采集器进程组；根进程已退出时仍清理可能存活的后代。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            env=_collector_env(),
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _split(command: str) -> list[str]:
    if any(char in command for char in ("\x00", "\r", "\n")):
        raise AdapterError("collector command contains forbidden characters")
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise AdapterError("collector command cannot be parsed") from exc
    if os.name == "nt":
        parts = [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part for part in parts]
    if not parts:
        raise AdapterError("collector command is not configured")
    return parts


class CommandAdapter(CollectorAdapter):
    def __init__(self, command: str) -> None:
        self._argv = _split(command)

    def collect(
        self,
        task: TaskAssignment,
        credential: Credential,
        output_dir: Path,
        stop_event: Event | None = None,
    ) -> Path:
        output_path = (output_dir / "result.csv").resolve()
        env = _collector_env()
        env.update(
            username=credential.username,
            password=credential.password,
            target_date=task.target_date.isoformat(),
            output_path=str(output_path),
        )
        process: subprocess.Popen[bytes] | None = None
        windows_job: _WindowsJob | None = None
        try:
            if stop_event is not None and stop_event.is_set():
                raise AdapterError("collector stopped by shutdown request")
            process = subprocess.Popen(
                self._argv,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | _CREATE_SUSPENDED
                    if os.name == "nt"
                    else 0
                ),
            )
            if os.name == "nt":
                windows_job = _WindowsJob.attach(process)
                if windows_job is None:
                    _terminate_process_tree(process)
                    raise AdapterError(
                        "collector could not be isolated in a Windows Job Object"
                    )
                try:
                    _resume_windows_process(process)
                except OSError as exc:
                    windows_job.close()
                    windows_job = None
                    _terminate_process_tree(process)
                    raise AdapterError(
                        "collector suspended process could not be resumed"
                    ) from exc
            deadline = time.monotonic() + _COMMAND_TIMEOUT
            while True:
                if stop_event is not None and stop_event.is_set():
                    _terminate_process_tree(process)
                    raise AdapterError("collector stopped by shutdown request")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_process_tree(process)
                    raise AdapterError("collector command timed out")
                try:
                    returncode = process.wait(timeout=min(1.0, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except OSError as exc:
            raise AdapterError("collector command could not be started") from exc
        finally:
            env.pop("password", None)
            # Job Object 即使根进程已退出也会终止仍存活的后代；进程在挂入
            # Job 前保持 suspended，分配失败则 fail-closed，不执行采集命令。
            if windows_job is not None:
                windows_job.close()
            if process is not None:
                _terminate_process_tree(process)

        if returncode != 0:
            raise AdapterError(f"collector exited with code {returncode}")
        try:
            if not output_path.is_file() or output_path.stat().st_size <= 0:
                raise AdapterError("collector output is missing or empty")
        except OSError as exc:
            raise AdapterError("collector output cannot be inspected") from exc
        return output_path
