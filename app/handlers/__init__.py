from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from aiogram import Bot, Dispatcher


def register_handlers(dp: Dispatcher, bot: Bot) -> None:
    package_dir = str(Path(__file__).parent)
    # Name order is the dispatch order: `llm` claims private chat text before
    # `search` sees it. Directory order alone would leave that to the filesystem.
    for info in sorted(pkgutil.iter_modules([package_dir]), key=lambda item: item.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        register = getattr(module, "register", None)
        if callable(register):
            register(dp, bot)
