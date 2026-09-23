"""Loading, layering, and validating style templates.

Values are layered, each layer overriding the one before (docs/style-templates.md):

1. the defaults in :class:`~.model.Style`
2. the chain of templates named by ``extends``, base first
3. the chosen template
4. per-job overrides (``--set key=value`` or ``RenderOptions.overrides``)

The merged mapping is then checked against the dataclasses in :mod:`.model`.
Every problem is collected, with its key path, before :class:`StyleError` is
raised, so one run reports everything that is wrong with a template.
"""
from __future__ import annotations

import copy
import dataclasses
import difflib
import types
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

import yaml

from . import model
from .model import Style

STYLE_FORMAT_VERSION = 1


class StyleError(ValueError):
    """A style template or override is invalid. ``problems`` lists each
    problem with its key path."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.source = source
        self.problems = problems
        details = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"Invalid style {source}:\n{details}")


def builtin_styles() -> list[str]:
    """Names of the style templates shipped with the package."""
    folder = resources.files(__package__).joinpath("builtin")
    return sorted(item.name[: -len(".yaml")] for item in folder.iterdir() if item.name.endswith(".yaml"))


def load_style(
    style: str | Path | Style | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> Style:
    """The validated :class:`Style` for *style* — a built-in name, a path to
    a YAML file, or a :class:`Style` — with *overrides* applied.

    *overrides* maps dotted keys to values, e.g.
    ``{"numbering.scheme": "mixed", "locale": "cs-CZ"}``; nested mappings are
    accepted too.
    """
    if isinstance(style, Style):
        data, source = style_to_dict(style), f"'{style.name}'"
    else:
        data, source = _load_chain(style if style is not None else "default", seen=())
    if overrides:
        data = _merge(data, _expand_dotted(overrides))
    return _build(data, source)


def style_to_dict(style: Style) -> dict[str, Any]:
    """*style* as plain data, in the shape a YAML style template is written."""
    return _plain(style)


def dump_style(style: Style) -> str:
    """*style* as a complete YAML style template."""
    return yaml.safe_dump(style_to_dict(style), sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_chain(spec: str | Path, *, seen: tuple[str, ...], relative_to: Path | None = None) -> tuple[dict, str]:
    """The merged raw mapping for *spec* and every template it extends."""
    raw, source, folder = _read(spec, relative_to)
    if source in seen:
        chain = " -> ".join((*seen, source))
        raise StyleError(source, [f"extends: circular chain {chain}"])
    parent = raw.get("extends")
    if parent is None:
        if source == "'default'":
            # Start from every built-in default, so that a partial mapping —
            # one heading level's alignment, say — merges field by field.
            return _merge(style_to_dict(Style()), raw), source
        base, _ = _load_chain("default", seen=(*seen, source))
    elif isinstance(parent, str) and parent:
        base, _ = _load_chain(parent, seen=(*seen, source), relative_to=folder)
    else:
        raise StyleError(source, ["extends: must be a style name or a path"])
    return _merge(base, raw), source


def _read(spec: str | Path, relative_to: Path | None) -> tuple[dict, str, Path | None]:
    """The raw mapping in *spec*, a label for messages, and the folder that
    relative ``extends`` paths are resolved against."""
    name = str(spec)
    is_path = isinstance(spec, Path) or name.endswith((".yaml", ".yml")) or "/" in name or "\\" in name
    if not is_path:
        if name not in builtin_styles():
            known = ", ".join(builtin_styles())
            raise StyleError(f"'{name}'", [f"no built-in style named '{name}' (built-in: {known}); "
                                           f"to load a file, give a path ending in .yaml"])
        text = resources.files(__package__).joinpath("builtin", f"{name}.yaml").read_text("utf-8")
        return _parse(text, f"'{name}'"), f"'{name}'", None
    path = Path(spec)
    if relative_to is not None and not path.is_absolute():
        path = relative_to / path
    try:
        text = path.read_text("utf-8")
    except OSError as error:
        raise StyleError(str(path), [f"cannot read the file: {error.strerror or error}"]) from None
    raw = _parse(text, str(path))
    raw.setdefault("name", path.stem)
    return raw, str(path), path.parent


def _parse(text: str, source: str) -> dict:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise StyleError(source, [f"not valid YAML: {error}"]) from None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise StyleError(source, ["a style template must be a YAML mapping"])
    version = raw.get("version", STYLE_FORMAT_VERSION)
    if version != STYLE_FORMAT_VERSION:
        raise StyleError(source, [f"version: this renderer reads style format {STYLE_FORMAT_VERSION}, got {version!r}"])
    return raw


def _merge(base: Mapping, override: Mapping) -> dict:
    """*override* deep-merged onto *base*; mappings merge, anything else
    (lists included) replaces."""
    merged = {str(key): value for key, value in base.items()}
    for key, value in override.items():
        key = str(key)
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _expand_dotted(overrides: Mapping[str, Any]) -> dict:
    """*overrides* as one nested mapping. Dotted and nested keys for the
    same setting merge in order, and the caller's values are copied, never
    modified."""
    expanded: dict = {}
    for key, value in overrides.items():
        target = expanded
        *parents, last = str(key).split(".")
        for part in parents:
            target = target.setdefault(part, {})
            if not isinstance(target, dict):
                raise StyleError("overrides", [f"{key}: '{part}' is set to a value and cannot hold keys"])
        value = _copy(value)
        if isinstance(value, dict) and isinstance(target.get(last), dict):
            value = _merge(target[last], value)
        target[last] = value
    return expanded


def _copy(value: Any) -> Any:
    """A deep copy of *value* with every mapping made a plain dict."""
    if isinstance(value, Mapping):
        return {str(key): _copy(item) for key, item in value.items()}
    return copy.deepcopy(value)


def parse_override(key: str, text: str) -> Any:
    """The value for setting *key* written as *text* on a command line.

    Text settings take *text* as written, so ``[_____]`` or ``{designation}``
    stay text. A value wrapped in YAML quotes (``"none"``, ``' | '``) is
    unquoted; to keep quotation marks in the value, wrap it in the other kind
    (``'"{designation}"'``). Other settings read it as YAML, so that
    ``true``, ``2``, or ``[...]`` mean what they say. ``null`` clears a
    setting that may be unset, such as ``locale``. Raises StyleError for a
    quoted value that is not valid YAML.
    """
    hint = _hint_for(key.split("."))
    if hint is not None and _is_text(hint):
        stripped = text.strip()
        if type(None) in get_args(hint) and stripped in ("null", "~"):
            return None
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "'\"":
            try:
                quoted = yaml.safe_load(stripped)
            except yaml.YAMLError as error:
                problem = str(error).splitlines()[0]
                raise StyleError("setting", [f"{key}: {stripped} is not a valid quoted value ({problem})"]) from None
            if isinstance(quoted, str):
                return quoted
        return text
    try:
        return yaml.safe_load(text) if text.strip() else ""
    except yaml.YAMLError:
        return text


def _hint_for(parts: list[str]) -> Any:
    """The declared type of the setting at the dotted path *parts*, or None."""
    hint: Any = Style
    for part in parts:
        if dataclasses.is_dataclass(hint):
            hints = get_type_hints(hint, vars(model))
            if part not in hints:
                return None
            hint = hints[part]
        elif get_origin(hint) is dict:
            hint = get_args(hint)[1]
        else:
            return None
    return hint


def _is_text(hint: Any) -> bool:
    if hint is str:
        return True
    origin = get_origin(hint)
    if origin is Literal:
        return all(isinstance(arg, str) for arg in get_args(hint))
    if origin in (Union, types.UnionType):
        options = [arg for arg in get_args(hint) if arg is not type(None)]
        return bool(options) and all(_is_text(arg) for arg in options)
    return False


# ---------------------------------------------------------------------------
# Validation: raw data -> dataclasses
# ---------------------------------------------------------------------------


def _build(data: Mapping, source: str) -> Style:
    problems: list[str] = []
    style = _convert(data, Style, "", problems)
    if problems:
        raise StyleError(source, problems)
    return style


def _convert(value: Any, hint: Any, path: str, problems: list[str]) -> Any:
    label = path or "(root)"
    origin = get_origin(hint)
    if dataclasses.is_dataclass(hint):
        return _convert_dataclass(value, hint, path, problems)
    if origin is Literal:
        allowed = get_args(hint)
        if value not in allowed:
            problems.append(f"{label}: must be one of {', '.join(map(str, allowed))} (got {value!r})")
            return allowed[0]
        return value
    if origin in (Union, types.UnionType):
        options = [arg for arg in get_args(hint) if arg is not type(None)]
        if value is None and type(None) in get_args(hint):
            return None
        return _convert(value, options[0], path, problems)
    if origin is tuple:
        item_hint = get_args(hint)[0]
        if not isinstance(value, (list, tuple)):
            problems.append(f"{label}: must be a list")
            return ()
        return tuple(_convert(item, item_hint, f"{path}[{index}]", problems) for index, item in enumerate(value))
    if origin is dict:
        key_hint, item_hint = get_args(hint)
        if not isinstance(value, Mapping):
            problems.append(f"{label}: must be a mapping")
            return {}
        converted = {}
        for key, item in value.items():
            if key_hint is int:
                try:
                    key = int(key)
                except (TypeError, ValueError):
                    problems.append(f"{path}.{key}: key must be a number")
                    continue
                if not 1 <= key <= 6:
                    problems.append(f"{path}.{key}: heading levels are 1 to 6")
                    continue
            converted[key] = _convert(item, item_hint, f"{path}.{key}", problems)
        return converted
    if hint is bool:
        if not isinstance(value, bool):
            problems.append(f"{label}: must be true or false (got {value!r})")
            return False
        return value
    if hint is int:
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append(f"{label}: must be a whole number (got {value!r})")
            return 0
        return value
    if hint is str:
        # YAML reads 700 or 1.5 as numbers; they are fine as text here.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if not isinstance(value, str):
            problems.append(f"{label}: must be text (got {value!r})")
            return ""
        return value
    raise TypeError(f"unsupported style field type {hint!r} at {label}")  # a bug in model.py


def _convert_dataclass(value: Any, cls: type, path: str, problems: list[str]) -> Any:
    label = path or "(root)"
    if isinstance(value, cls):
        return value
    if not isinstance(value, Mapping):
        problems.append(f"{label}: must be a mapping")
        return cls()
    hints = get_type_hints(cls, vars(model))
    known = {item.name: item for item in dataclasses.fields(cls)}
    values = {}
    for key, item in value.items():
        key = str(key)
        where = f"{path}.{key}" if path else key
        if key not in known:
            suggestion = _closest(key, list(known))
            problems.append(f"{where}: unknown key" + (f" (did you mean '{suggestion}'?)" if suggestion else ""))
            continue
        values[key] = _convert(item, hints[key], where, problems)
    return cls(**values)


def _closest(key: str, candidates: list[str]) -> str | None:
    matches = difflib.get_close_matches(key, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in dataclasses.fields(value)}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


__all__ = [
    "StyleError",
    "builtin_styles",
    "dump_style",
    "load_style",
    "parse_override",
    "style_to_dict",
]
