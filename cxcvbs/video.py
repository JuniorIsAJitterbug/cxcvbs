import ctypes
import os
import numpy as np
from PIL import Image

from sdl2 import *

if os.name == "nt":
    import win32file

screenw, screenh = 1680, 1050
overscan = 0


class Video:
    def __init__(self, cxadc, registers, sample_rate=28636363, refresh=25, lines=625, show_regs=True):
        self._cxadc = cxadc
        self._registers = registers
        self._lines = lines
        self._cmdthread = None
        self.show_regs = show_regs
        self._sample_rate = sample_rate
        self.set_standard(refresh, lines)
        self.screenshot = False

        SDL_Init(SDL_INIT_VIDEO)
        self._window = SDL_CreateWindow(b"cxcvbs",
                                  SDL_WINDOWPOS_UNDEFINED,
                                  SDL_WINDOWPOS_UNDEFINED,
                                  1680, 1050, SDL_WINDOW_SHOWN)

        SDL_ShowCursor(False)
        self._renderer = SDL_CreateRenderer(self._window, -1, 0)
        self._texture = SDL_CreateTexture(self._renderer, SDL_PIXELFORMAT_RGB888, SDL_TEXTUREACCESS_STREAMING,
                                    2048, 1024)
        SDL_SetTextureScaleMode(self._texture, SDL_ScaleModeBest)
        self._palette = np.repeat(np.arange(256, dtype=np.uint8).reshape(-1, 1), 4, axis=1)

        self._palette[0] = (0, 0, 255, 255)
        for n in range(1, 64):
            self._palette[n] = (n*4, 0, 0, 255)
        self._palette[255] = (0, 255, 0, 255)

    def calculate_timings(self):
        self._samples_per_line = round(self._sample_rate / (self._refresh * self._lines))
        self._samples_per_frame = int(self._sample_rate / self._refresh)
        self._frac = (self._sample_rate / self._refresh) - self._samples_per_frame

    def set_standard(self, refresh, lines):
        self._refresh = refresh
        self._lines = lines
        self.calculate_timings()

    def set_sample_rate(self, rate):
        self._sample_rate = rate
        self.calculate_timings()

    @property
    def sample_rate(self):
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, rate):
        self._sample_rate = rate
        self.calculate_timings()

    def draw_bits(self, data, xpos, ypos):
        for y, i in enumerate(data):
            SDL_SetRenderDrawColor(self._renderer, 255, 255, 255, 255)
            for x in range(32):
                b = (i >> x) & 1
                xp = xpos + (x * 16) + ((x // 8) * 4)
                yp = ypos + (y * 16) + ((y // 4) * 4)
                (SDL_RenderFillRect if b else SDL_RenderDrawRect)(self._renderer, SDL_Rect(xp, yp, 14, 14))

    def draw_histogram(self, data, xpos=1144, ypos=1030):
        histogram = np.bincount(data)
#        histogram = np.sqrt(histogram)
#        histogram = ((histogram * 200) // np.max(histogram)).astype(np.uint32)
        histogram = np.clip(histogram>>6, 0, 200)
        SDL_SetRenderDrawColor(self._renderer, 0, 0, 255, 255)
        SDL_RenderDrawLine(self._renderer, xpos + 128, ypos, xpos + 128, ypos - 200)
        SDL_SetRenderDrawColor(self._renderer, 255, 255, 255, 255)
        SDL_RenderDrawLine(self._renderer, xpos, ypos, xpos + 512, ypos)
        for n in range(histogram.shape[0]):
            SDL_RenderDrawLine(self._renderer, xpos + (n * 2), ypos, xpos + (n * 2),
                               ypos - histogram[n])


    def run(self):

        c = 0

        running = True
        event = SDL_Event()

        while running:
            if self._cmdthread and not self._cmdthread.is_alive():
                self._cmdthread.join()
                break

            while SDL_PollEvent(ctypes.byref(event)) != 0:
                if event.type == SDL_QUIT:
                    running = False
                    break
                elif event.type == SDL_KEYUP:
                    print(event.key)

            if os.name == "nt":
                read_hr, read_buf = win32file.ReadFile(self._cxadc, self._samples_per_frame)
                data = np.frombuffer(read_buf, dtype=np.uint8)
            else:
                data = np.frombuffer(self._cxadc.read(self._samples_per_frame), dtype=np.uint8)

            c += self._frac
            while c > 1:
                c -= 1
                if os.name == "nt":
                    win32file.ReadFile(self._cxadc, 1)
                else:
                    self._cxadc.read(1)
            img = self._palette[data]
            SDL_SetRenderDrawColor(self._renderer, 0, 0, 0, 255)
            SDL_RenderClear(self._renderer)
            SDL_UpdateTexture(self._texture, SDL_Rect(0, 0, self._samples_per_line, self._lines), img.tobytes(), self._samples_per_line*4)
            SDL_RenderCopy(self._renderer, self._texture, SDL_Rect(0, 0, self._samples_per_line, self._lines), SDL_Rect(0, 0, screenw, self._lines if self.show_regs else screenh))

            if self.show_regs:
                self.draw_bits(self._registers.read_block(0x310100, 0x60), 20, 636)
                self.draw_bits(self._registers.read_block(0x310160, 0x4c), 20 + (1680//3), 636)
                self.draw_bits(self._registers.read_block(0x310200, 0x28), 20 + (2*1680//3), 636)

                self.draw_histogram(data)

            SDL_RenderPresent(self._renderer)

            if self.screenshot:
                buffer = np.zeros((screenh, screenw, 4), dtype=np.uint8)
                SDL_RenderReadPixels(self._renderer, SDL_Rect(0, 0, screenw, screenh), SDL_PIXELFORMAT_ABGR8888, buffer.ctypes.data, screenw*4)
                Image.fromarray(buffer).save("screenshot.png")
                self.screenshot = False

        SDL_DestroyWindow(self._window)
        return 0

