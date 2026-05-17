"""Implementation namespace loader for orchestration run_store compatibility surface."""

from __future__ import annotations

from pathlib import Path as _Path

_IMPL_PARTS = ('_run_store_impl_part01.py', '_run_store_impl_part02.py', '_run_store_impl_part03.pyfrag', '_run_store_impl_part04.py', '_run_store_impl_part05.pyfrag', '_run_store_impl_part06.py')
_IMPL_DIR = _Path(__file__).resolve().parent

for _part_name in _IMPL_PARTS:
    _part_path = _IMPL_DIR / _part_name
    _source = _part_path.read_text(encoding="utf-8")
    exec(compile(_source, str(_part_path), "exec"), globals())

del _Path, _IMPL_DIR, _IMPL_PARTS, _part_name, _part_path, _source
