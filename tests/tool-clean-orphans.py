# -*- coding: utf-8 -*-
"""清理孤儿 msedgewebview2 进程（父进程已退出、被遗弃的 WebView2 进程树根）。

用途：验证单文件 exe 的"关窗即全停"是否生效。正常情况下应当清理到 0 个。
"""
import ctypes
import ctypes.wintypes as wt

k = ctypes.windll.kernel32
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def snapshot() -> dict:
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
    out = {}
    ok = k.Process32First(snap, ctypes.byref(entry))
    while ok:
        out[entry.th32ProcessID] = (entry.szExeFile.decode("gbk", "replace"),
                                    entry.th32ParentProcessID)
        ok = k.Process32Next(snap, ctypes.byref(entry))
    k.CloseHandle(snap)
    return out


def count_webview() -> int:
    return sum(1 for name, _ in snapshot().values() if name.lower() == "msedgewebview2.exe")


def clean_orphans() -> int:
    procs = snapshot()
    alive = set(procs)
    killed = []
    for pid, (name, ppid) in procs.items():
        if name.lower() != "msedgewebview2.exe":
            continue
        if ppid in alive:      # 父进程还在 → 不是孤儿
            continue
        handle = k.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            if k.TerminateProcess(handle, 0):
                killed.append(pid)
            k.CloseHandle(handle)
    return len(killed)


if __name__ == "__main__":
    print("清理前 WebView2 进程数:", count_webview())
    n = clean_orphans()
    print("已终止孤儿进程:", n)
    import time
    time.sleep(2)
    print("清理后 WebView2 进程数:", count_webview())
