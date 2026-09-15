"""Read the user's installed PoB2 data; never download or mix PoE1 tables.

Data values are source definitions, not calculated character effects. Only local
PoB Lua data files run, with no filesystem, process or network Lua globals.
"""
from __future__ import annotations
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from lupa.luajit21 import LuaRuntime, lua_type


def values(table):
    return list(table.values()) if isinstance(table, dict) else list(table or [])


def _plain(value):
    if lua_type(value) == 'table':
        return {k: _plain(v) for k, v in value.items()}
    if lua_type(value) == 'function':
        raise ValueError('Executable callback in data table cannot be represented as data')
    return value


def _path(value: str) -> Path:
    if os.name != 'nt' and re.match(r'^[A-Za-z]:[\\/]', value):
        return Path('/mnt') / value[0].lower() / value[3:].replace('\\', '/')
    return Path(value).expanduser()


class LocalPoBData:
    def __init__(self, root: str | Path):
        self.root = _path(str(root)).resolve()
        if not (self.root / 'Data/Gems.lua').is_file():
            if (self.root / 'src/Data/Gems.lua').is_file():
                self.root = self.root / 'src'
            else:
                raise FileNotFoundError('PoB2 Data/Gems.lua missing; set POB_INSTALL_DIR to a complete PoB2 installation')
        if not any((self.root / 'TreeData').glob('0_*')):
            raise FileNotFoundError('PoB2 TreeData/0_* missing; a PoE1 installation cannot supply PoE2 data')
        self._cache: dict[tuple, Any] = {}

    def _file(self, relative: str) -> Path:
        p = (self.root / relative).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError('Data path escapes PoB2 installation')
        return p

    def metadata(self, relative: str) -> dict:
        p = self._file(relative)
        return {'game': 'poe2', 'source': 'local PoB2', 'file': relative,
                'modified_at': datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()}

    def load(self, relative: str, seed: bool = False) -> dict:
        p = self._file(relative)
        stat = p.stat()
        key = (str(p), stat.st_mtime_ns, stat.st_size, seed)
        if key not in self._cache:
            runtime = LuaRuntime(unpack_returned_tuples=True, register_eval=False, register_builtins=False)
            # These are trusted installed data tables, not remote Lua or build code.
            loader = runtime.eval('function(source, seed) local f,e=loadstring(source); if not f then error(e) end; setfenv(f, {}); local result=f(seed); return result or seed end')
            result = loader(p.read_text(encoding='utf-8-sig'), runtime.table() if seed else None)
            result = _plain(result)
            if not isinstance(result, dict):
                raise ValueError(f'Expected a data table from {relative}')
            self._cache = {k: v for k, v in self._cache.items() if k[0] != str(p)}
            self._cache[key] = result
        return self._cache[key]

    def gems(self) -> list[dict]:
        return sorted([dict(v, id=k) for k, v in self.load('Data/Gems.lua').items()], key=lambda x: x['name'])

    def bases(self) -> list[dict]:
        result = []
        for p in sorted((self.root / 'Data/Bases').glob('*.lua')):
            data = self.load(str(p.relative_to(self.root)), seed=True)
            result.extend(dict(v, name=k) for k, v in data.items())
        return result

    def tree(self, version: str = '') -> dict:
        if version and not re.fullmatch(r'0_\d+(?:_\d+)*', version):
            raise ValueError('Only PoE2 tree versions 0_* are supported')
        if not version:
            versions = [p.name for p in (self.root / 'TreeData').iterdir()
                        if re.fullmatch(r'0_\d+(?:_\d+)*', p.name) and (p/'tree.lua').is_file()]
            if not versions:
                raise FileNotFoundError('No complete PoB2 passive tree found')
            version = max(versions, key=lambda x: tuple(map(int, x.split('_'))))
        data = self.load(f'TreeData/{version}/tree.lua')
        return dict(data, version=version)

    def uniques(self) -> list[dict]:
        result = []
        for p in sorted((self.root / 'Data/Uniques').glob('*.lua')):
            for raw in values(self.load(str(p.relative_to(self.root)))):
                if not isinstance(raw, str):
                    continue
                lines = [s.strip() for s in raw.strip().splitlines() if s.strip()]
                if len(lines) < 2:
                    continue
                variant_names = [s[8:].strip() for s in lines if s.startswith('Variant:')]
                implicit_idx = next((i for i,s in enumerate(lines) if s.startswith('Implicits:')), None)
                mod_start = implicit_idx + 1 if implicit_idx is not None else 2
                variants = []
                for vid, name in enumerate(variant_names or ['Default'], start=1):
                    mods = []
                    for line in lines[mod_start:]:
                        tag = re.search(r'\{variant:([\d,]+)\}', line)
                        if tag and vid not in [int(v) for v in tag[1].split(',')]:
                            continue
                        if line in {'Corrupted','Mirrored','Unreleased'} or re.match(r'^(?:League|Source|Variant|Selected Variant|Has Alt Variant(?: Two)?|Alt Variant(?: Two)?|Crafted|Quality|Item Level|LevelReq|Sockets):',line):
                            continue
                        mods.append(re.sub(r'\{[^}]*\}', '', line))
                    variants.append({'id':vid,'name':name,'modifiers':mods})
                result.append({'name':lines[0], 'base_type':lines[1], 'variants':variants,
                               'implicit_count':int(lines[implicit_idx].split(':',1)[1]) if implicit_idx is not None else 0,
                               'source':self.metadata(str(p.relative_to(self.root)))})
        return result


@lru_cache(maxsize=4)
def _get(root):
    return LocalPoBData(root)


def get_data() -> LocalPoBData:
    root = os.environ.get('POB_INSTALL_DIR')
    if not root:
        appdata = os.environ.get('APPDATA')
        root = str(Path(appdata)/'Path of Building Community (PoE2)') if appdata else ''
    if not root:
        raise FileNotFoundError('Set POB_INSTALL_DIR to the local Path of Building Community (PoE2) directory')
    return _get(root)
