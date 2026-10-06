"""Acesso à memória de um processo 32-bit (WOW64) via WinAPI/ctypes."""
import ctypes
import ctypes.wintypes as wt
import re
import struct

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_ALL_ACCESS = 0x1F0FFF
TH32CS_SNAPPROCESS = 0x2
TH32CS_SNAPMODULE = 0x8
TH32CS_SNAPMODULE32 = 0x10
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
PAGE_EXECUTE_READWRITE = 0x40


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD),
                ("modBaseAddr", ctypes.c_void_p), ("modBaseSize", wt.DWORD),
                ("hModule", wt.HMODULE), ("szModule", ctypes.c_wchar * 256),
                ("szExePath", ctypes.c_wchar * 260)]


k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.OpenProcess.restype = wt.HANDLE
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualProtectEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD,
                                 ctypes.POINTER(wt.DWORD)]
k32.FlushInstructionCache.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t]
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]


def find_pid(name):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(e)
    try:
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == name.lower():
                return e.th32ProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return None


class Process:
    def __init__(self, exe_name):
        self.pid = find_pid(exe_name)
        if not self.pid:
            raise RuntimeError(f"Processo {exe_name} não encontrado")
        self.h = k32.OpenProcess(PROCESS_ALL_ACCESS, False, self.pid)
        if not self.h:
            raise ctypes.WinError(ctypes.get_last_error())
        self.base, self.size = self._module(exe_name)

    def _module(self, name):
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid)
        e = MODULEENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        try:
            ok = k32.Module32FirstW(snap, ctypes.byref(e))
            while ok:
                if e.szModule.lower() == name.lower():
                    return e.modBaseAddr, e.modBaseSize
                ok = k32.Module32NextW(snap, ctypes.byref(e))
        finally:
            k32.CloseHandle(snap)
        raise RuntimeError("Módulo não encontrado")

    def alive(self):
        code = wt.DWORD()
        return bool(k32.GetExitCodeProcess(self.h, ctypes.byref(code))) and code.value == 259

    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, addr, buf, n, ctypes.byref(got)):
            raise OSError(f"ReadProcessMemory falhou em {addr:#x}")
        return buf.raw

    def write(self, addr, data, code=False):
        old = wt.DWORD()
        if code:
            k32.VirtualProtectEx(self.h, addr, len(data), PAGE_EXECUTE_READWRITE, ctypes.byref(old))
        got = ctypes.c_size_t()
        ok = k32.WriteProcessMemory(self.h, addr, data, len(data), ctypes.byref(got))
        if code:
            k32.VirtualProtectEx(self.h, addr, len(data), old.value, ctypes.byref(old))
            k32.FlushInstructionCache(self.h, addr, len(data))
        if not ok:
            raise OSError(f"WriteProcessMemory falhou em {addr:#x}")

    def u8(self, a): return self.read(a, 1)[0]
    def u16(self, a): return struct.unpack("<H", self.read(a, 2))[0]
    def u32(self, a): return struct.unpack("<I", self.read(a, 4))[0]
    def i32(self, a): return struct.unpack("<i", self.read(a, 4))[0]
    def w8(self, a, v): self.write(a, struct.pack("<B", v))
    def w16(self, a, v): self.write(a, struct.pack("<H", v))
    def w32(self, a, v): self.write(a, struct.pack("<I", v))

    def alloc(self, n):
        p = k32.VirtualAllocEx(self.h, None, n, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)
        if not p:
            raise ctypes.WinError(ctypes.get_last_error())
        return p

    _image = None

    def image(self):
        if self._image is None:
            chunks = []
            for off in range(0, self.size, 0x1000):
                try:
                    chunks.append(self.read(self.base + off, 0x1000))
                except OSError:
                    chunks.append(b"\0" * 0x1000)
            self._image = b"".join(chunks)
        return self._image

    def scan(self, pattern):
        """Pattern no estilo "8B 0D ? ? ? ? 51" -> lista de endereços absolutos."""
        rx = b"".join(b"." if t == "?" else re.escape(bytes([int(t, 16)])) for t in pattern.split())
        return [self.base + m.start() for m in re.finditer(rx, self.image(), re.DOTALL)]

    def scan1(self, pattern):
        hits = self.scan(pattern)
        if len(hits) != 1:
            raise RuntimeError(f"Pattern '{pattern}' encontrou {len(hits)} resultados")
        return hits[0]

    def call_target(self, addr):
        """Destino de um E8/E9 rel32 em addr."""
        return (addr + 5 + struct.unpack("<i", self.read(addr + 1, 4))[0]) & 0xFFFFFFFF
