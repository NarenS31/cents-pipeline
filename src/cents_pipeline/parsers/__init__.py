from __future__ import annotations

from pathlib import Path

from .base import DistrictParser

_REGISTRY: dict[str, type[DistrictParser]] = {}


def register(cls: type[DistrictParser]) -> type[DistrictParser]:
    """Class decorator: make a Python parser selectable by name (`--parser <name>`)."""
    _REGISTRY[cls.name] = cls
    return cls


def load_parser(spec: str) -> DistrictParser:
    """`spec` is a registered parser name or a path to a YAML config."""
    if spec in _REGISTRY:
        return _REGISTRY[spec]()
    p = Path(spec)
    if p.suffix in {".yml", ".yaml"} and p.exists():
        from .yaml_parser import YAMLDistrictParser  # needs the optional `cli` extra (PyYAML)

        return YAMLDistrictParser.from_file(p)
    raise ValueError(f"unknown parser {spec!r}; registered: {sorted(_REGISTRY)}")


__all__ = ["DistrictParser", "YAMLDistrictParser", "load_parser", "register"]
