import click
import os
import pathlib

if os.name == "nt":
    import win32file

from . import memory
from . import video
from . import command


@click.command()
@click.option('-d', '--device', default='/dev/cxadc0')
@click.option('--video/--no-video', 'show_video', default=True, help='Show video on screen')
@click.option('--regs/--no-regs', 'show_regs', default=True, help='Show registers on video')
@click.option('-x', '--xtal', type=int, default=28636383)
@click.option('-s', '--standard', type=click.Choice(('PAL', 'NTSC')), default='PAL')
def main(device, show_video, show_regs, xtal, standard):
    size = 0x400000

    if os.name == "nt":
        mem = memory.WindowsMemory(device, size)
        print(device, 'opened')
    else:
        rdev = os.stat(device).st_rdev
        major = rdev >> 8
        minor = rdev & 0xff
        pcires = pathlib.Path('/sys/dev/char/') / f'{major}:{minor}' / 'device/resource0'
        mem = memory.Memory(pcires, size)
        print(pcires, 'opened')

    with mem:
        if show_video:
            if os.name == "nt":
                dev = win32file.CreateFile(
                    device,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None)
            else:
                dev = open(device, 'rb')
            print(device, 'opened')
            if standard == 'PAL':
                vid = video.Video(dev, mem, sample_rate=xtal, refresh=25, lines=625, show_regs=show_regs)
            else:
                vid = video.Video(dev, mem, sample_rate=xtal, refresh=29.97, lines=525, show_regs=show_regs)
        else:
            vid = None
        th = command.run_thread(mem, vid)
        if vid:
            vid._cmdthread = th
            vid.run()
        else:
            th.join()

    if dev:
        if os.name == "nt":
            win32file.CloseHandle(dev)
        else:
            dev.close()

    print("Exiting")


if __name__ == '__main__':
    main()
