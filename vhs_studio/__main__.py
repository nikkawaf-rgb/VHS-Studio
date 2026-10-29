# -*- coding: utf-8 -*-
"""Точка входа: python -m vhs_studio"""
import sys

from .device import FFMPEG
from .ui import App


def main():
    if not FFMPEG:
        print("ffmpeg не найден в PATH")
    App().mainloop()


if __name__ == "__main__":
    main()
