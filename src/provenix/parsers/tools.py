"""Reader for `data/stochastic_tools.yml`.

Deliberately not a general YAML parser and not PyYAML. Provenix has no
third-party dependencies, which matters for a tool people point at untrusted
repositories, and this file has a fixed schema that Provenix itself ships and
controls:

    tools:
      - name: preseq
        invoked_as: 'preseq\\s+lc_extrap'
        flags: ['-seed', '--seed']
        why: ...

Anything outside that shape is ignored rather than guessed at. Third-party
YAML — conda environment files — is handled by conda_env.py, which is
similarly narrow and reads only the `dependencies:` block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "stochastic_tools.yml"

_ITEM = re.compile(r"^\s*-\s+name\s*:\s*(.+?)\s*$")
_FIELD = re.compile(r"^\s+([a-z_]+)\s*:\s*(.+?)\s*$")
_LIST = re.compile(r"^\[(.*)\]$")


@dataclass(frozen=True)
class StochasticTool:
    name: str
    invoked_as: re.Pattern[str]
    flags: tuple[str, ...]
    why: str

    def is_invoked_in(self, text: str) -> bool:
        return bool(self.invoked_as.search(text))

    def is_seeded_in(self, text: str) -> bool:
        """True when any of this tool's seed flags appears in `text`.

        Matched as a whole token so `-s` does not match inside `--sample`.
        """
        for flag in self.flags:
            pattern = re.escape(flag) + r"(?=[\s=]|$)"
            if re.search(r"(?<![\w-])" + pattern, text):
                return True
        return False


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_list(value: str) -> tuple[str, ...]:
    match = _LIST.match(value.strip())
    if match is None:
        return (_unquote(value),)
    return tuple(_unquote(item) for item in match.group(1).split(",") if item.strip())


@lru_cache(maxsize=1)
def load(path: Path | None = None) -> tuple[StochasticTool, ...]:
    """Read the tool map. Cached, since it never changes during a run."""
    source = path or DATA_FILE
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return ()

    tools: list[StochasticTool] = []
    current: dict[str, str] = {}

    def flush() -> None:
        if not current.get("name") or not current.get("invoked_as"):
            return
        try:
            pattern = re.compile(current["invoked_as"])
        except re.error:
            return
        tools.append(
            StochasticTool(
                name=current["name"],
                invoked_as=pattern,
                flags=_parse_list(current.get("flags", "")),
                why=current.get("why", ""),
            )
        )

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = _ITEM.match(raw)
        if item is not None:
            flush()
            current = {"name": _unquote(item.group(1))}
            continue
        field = _FIELD.match(raw)
        if field is not None and current:
            current[field.group(1)] = _unquote(field.group(2))
    flush()

    return tuple(sorted(tools, key=lambda t: t.name))
