"""pytest 根 conftest — 保证项目根目录在 sys.path 中。

tests/ 下的用例使用 `from src.xxx import ...` 绝对导入，
pytest 默认只把 tests/ 目录加入 sys.path，会导致 ModuleNotFoundError。
根 conftest.py 存在时 pytest 会把项目根目录一并加入 sys.path。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
