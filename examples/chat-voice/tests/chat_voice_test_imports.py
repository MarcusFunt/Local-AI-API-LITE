from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = EXAMPLE_ROOT / "app"
PACKAGE_NAME = "chat_voice_app"

if PACKAGE_NAME not in sys.modules:
    package = ModuleType(PACKAGE_NAME)
    package.__path__ = [str(APP_DIR)]
    package.__package__ = PACKAGE_NAME
    package.__spec__ = importlib.util.spec_from_loader(PACKAGE_NAME, loader=None, is_package=True)
    sys.modules[PACKAGE_NAME] = package
