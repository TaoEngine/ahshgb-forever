# -*- coding: utf-8 -*-
"""兼容入口：``python main.py <子命令>`` 等价于 ``python -m ahshgb <子命令>``。"""
import sys

from ahshgb.cli import main

if __name__ == "__main__":
    sys.exit(main())
