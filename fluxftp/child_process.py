"""Keep the private Windows cloud helper scoped to the parent application's life."""
import os


def contain(process):
    if os.name != 'nt':
        return None
    import ctypes as c
    from ctypes import wintypes as w
    class Basic(c.Structure):
        _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64), ('flags', w.DWORD),
            ('min_ws', c.c_size_t), ('max_ws', c.c_size_t), ('active', w.DWORD),
            ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
    class IO(c.Structure):
        _fields_ = [(name, c.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
    class Extended(c.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_memory', c.c_size_t),
            ('job_memory', c.c_size_t), ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.AssignProcessToJobObject.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise OSError('Cannot create the cloud helper process container.')
    info = Extended()
    # Stop rclone with the app, but let an OAuth browser it launches live independently.
    info.basic.flags = 0x2000 | 0x1000  # KILL_ON_JOB_CLOSE | SILENT_BREAKAWAY_OK
    if not kernel.SetInformationJobObject(handle, 9, c.byref(info), c.sizeof(info)) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
        kernel.CloseHandle(handle)
        raise OSError('Cannot contain the cloud helper process.')
    return lambda: kernel.CloseHandle(handle)
