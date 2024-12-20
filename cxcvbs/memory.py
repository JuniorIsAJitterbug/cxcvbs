import ast
import textwrap

import mmap
import os
import pathlib

import yaml

if os.name == "nt":
    import win32file
    import winioctlcon
    import ctypes
    import struct

    CX_IOCTL_MMAP = 0xA00
    CX_IOCTL_MUNMAP = 0xA01


class RegisterException(Exception):
    pass

class AlignmentError(RegisterException):
    pass

class InvalidAddress(RegisterException):
    pass

class UnknownAddress(RegisterException):
    pass

class UnknownRegister(RegisterException):
    pass


class Cluster:
    _all = {}

    def __init__(self, description, addresses):
        self._description = description
        self._addresses = addresses
        self._registers = []
        self._sub_clusters = {}
        for addr, desc in self._addresses.items():
            if desc:
                self._sub_clusters[desc.split()[0].upper()] = Cluster(f'{description} : {desc}', {addr: None})
            else:
                if addr in self._all:
                    raise Exception("Duplicate address added to lookup table")
                self._all[addr] = self

    @property
    def addresses(self):
        yield from self._addresses.keys()

    def add_register(self, register):
        self._registers.append(register)

    @property
    def description(self):
        return '\n'.join([
            *(f'0x{addr:06x}: {self._description} {f"xxx_{desc.split()[0].upper()} : {desc}" if desc else ""}' for addr, desc in self._addresses.items()),
            *( '    ' + r.short_description for r in self._registers),
        ])

    @classmethod
    def by_addr(cls, addr):
        if isinstance(addr, str):
            addr = ast.literal_eval(addr)
            if not isinstance(addr, int):
                raise ValueError
        return cls._all[addr]

    def read(self, memory):
        return {f'0x{addr:06x}': memory.read_word(addr) for addr, desc in self._addresses.items()}

    def write(self, memory, value):
        for addr in self._addresses.keys():
            memory.write_word(addr, value, mask=0xffffffff)


class Register:
    _all = {}

    def __init__(self, name, cluster, *, description, mode, offset, length=1, **kwargs):
        self._name = name
        self._cluster = cluster
        self._description = description
        self._offset = offset
        self._length = length
        self._mode = mode
        if self._name in self._all:
            raise Exception("Duplicate register name added to lookup table")
        self._all[self._name] = self
        self._cluster.add_register(self)
        for name, subcluster in self._cluster._sub_clusters.items():
            Register(self._name+'_'+name, subcluster, description=description, mode=mode, offset=offset, length=length)

    @property
    def offset_description(self):
        if self._length == 1:
            d = str(self._offset)
        else:
            d = f'{self._offset}:{self._offset+self._length-1}'
        return f'{d:>5s}'

    @property
    def short_description(self):
        nl = "\n"
        return f'{self.offset_description} : {self._mode} : {self._name:8s} : {self._description.replace(nl, " ")}'

    @property
    def cluster_description(self):
        for addr, desc in self._cluster._addresses.items():
            yield f'0x{addr:06x} {self._mode}:{self.offset_description} : {self._name}{"_"+desc.split()[0].upper() if desc else ""}'

    @property
    def description(self):
        return '\n'.join([
            *self.cluster_description,
            textwrap.indent(self._description, "    "),
        ])

    @classmethod
    def by_name(cls, name):
        return cls._all[name.upper()]

    @property
    def mask(self):
        return ((1 << self._length) - 1) << self._offset

    def read(self, memory):
        return {f'{self._name}{"_"+desc.split()[0].upper() if desc else ""}': (memory.read_word(addr)&self.mask)>>self._offset for addr, desc in self._cluster._addresses.items()}

    def write(self, memory, value):
        for addr in self._cluster._addresses.keys():
            memory.write_word(addr, value<<self._offset, mask=self.mask)


data_file = pathlib.Path(__file__).parent / "cx23881.yaml"
reg_data = yaml.safe_load(data_file.open('r'))
for k, v in reg_data.items():
    c = Cluster(k, v['addresses'])
    for name, values in v['fields'].items():
        if name.lower() == 'reserved':
            continue
        r = Register(name, c, **values)


class RawAddress:
    def __init__(self, addr):
        self._addr = addr

    @property
    def description(self):
        return f'Raw address: 0x{self._addr:06x}'

    def read(self, memory):
        return {f'0x{self._addr:06x}': memory.read_word(self._addr)}

    def write(self, memory, value):
        memory.write_word(self._addr, value, mask=0xffffffff)


class WrappedMemory:
    def __init__(self, memory, object):
        self._memory = memory
        self._object = object

    @property
    def description(self):
        return self._object.description

    @property
    def value(self):
        return self._object.read(self._memory)

    @value.setter
    def value(self, val):
        self._object.write(self._memory, val)


class Memory(object):

    def __init__(self, filename, size):
        self._filename = filename
        self._size = size

    def __enter__(self):
        self._f = os.open(self._filename, os.O_RDWR | os.O_SYNC)
        self._mm = mmap.mmap(self._f, self._size)
        self._mv = memoryview(self._mm).cast('I')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        del self._mv
        self._mm.close()
        os.close(self._f)

    def read_word(self, address):
        if address & 0x3:
            raise AlignmentError
        return self._mv[address >> 2]

    def write_word(self, address, value, mask):
        if address & 0x3:
            raise AlignmentError
        self._mv[address >> 2] = (self._mv[address >> 2] & (~mask)) | (value & mask)

    def read_block(self, address, length):
        if address & 0x3:
            raise AlignmentError
        if length & 0x3:
            raise AlignmentError
        return self._mv[(address >> 2):(address >> 2) + (length >> 2)]

    def find(self, arg):
        object = None
        try:
            return WrappedMemory(self, Register.by_name(arg))
        except KeyError:
            pass
        try:
            return WrappedMemory(self, Cluster.by_addr(arg))
        except (KeyError, ValueError, SyntaxError):
            pass
        try:
            addr = ast.literal_eval(arg)
            if isinstance(addr, int) and 0 <= addr < self._size and (addr & 3) == 0:
                return WrappedMemory(self, RawAddress(addr))
        except (ValueError, SyntaxError):
            pass
        raise KeyError


class WindowsMemory(Memory):
    def __enter__(self):
        self._f = win32file.CreateFile(
            self._filename,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
            None,
            win32file.OPEN_EXISTING,
            0,
            None)

        byte_addr = win32file.DeviceIoControl(
            self._f,
            winioctlcon.CTL_CODE(
                winioctlcon.FILE_DEVICE_UNKNOWN,
                CX_IOCTL_MMAP,
                winioctlcon.METHOD_BUFFERED,
                winioctlcon.FILE_READ_DATA),
            None,
            8)

        # CX_IOCTL_MMAP returns a pointer to the shared memory space
        self._addr = struct.unpack("<Q", byte_addr)[0]
        self._mv = (ctypes.c_byte * self._size).from_address(self._addr)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        del self._mv

        win32file.DeviceIoControl(
            self._f,
            winioctlcon.CTL_CODE(
                winioctlcon.FILE_DEVICE_UNKNOWN,
                CX_IOCTL_MUNMAP,
                winioctlcon.METHOD_BUFFERED,
                winioctlcon.FILE_WRITE_DATA),
            None,
            0)

        win32file.CloseHandle(self._f)

    def read_word(self, address):
        if address & 0x3:
            raise AlignmentError

        return (ctypes.c_int).from_buffer(self._mv, address).value

    def write_word(self, address, value, mask):
        if address & 0x3:
            raise AlignmentError

        (ctypes.c_int).from_buffer(self._mv, address).value = (self.read_word(address) & (~mask) | (value & mask))

    def read_block(self, address, length):
        if address & 0x3:
            raise AlignmentError
        if length & 0x3:
            raise AlignmentError

        return (ctypes.c_int * (length // 4)).from_buffer(self._mv, address)[:]

if __name__ == '__main__':
    print(Register.by_name('vblank').description)
    print(Cluster.by_addr(0x310124).description)

