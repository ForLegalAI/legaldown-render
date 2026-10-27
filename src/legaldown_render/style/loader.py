"""Loading, layering, and validating style templates.

Values are layered, each layer overriding the one before (docs/style-templates.md):

1. the defaults in :class:`~.model.Style`
2. the chain of templates named by ``extends``, base first
3. the chosen template
4. per-job overrides (``--set key=value`` or ``RenderOptions.overrides``)

The merged mapping is then checked against the dataclasses in :mod:`.model`.
Every problem is collected, with its key path, before :class:`StyleError` is
raised, so one run reports everything that is wrong with a template.

A style is trusted input by default: the operator's own file. ``trusted=False``
(:func:`load_style`) is for a style that comes from someone else — it reads
no file, extends only built-in styles, holds every value that reaches the
stylesheet to a strict grammar, and never quotes the style in a message
(docs/architecture.md, Security).
"""
from __future__ import annotations

import copy
import dataclasses
import difflib
import os
import re
import types
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

import yaml

from ..errors import StyleError
from . import model
from .model import Style, css_problem, template_settings

STYLE_FORMAT_VERSION = 1

#: The longest YAML text an untrusted style may be (bytes of UTF-8).
MAX_UNTRUSTED_STYLE_BYTES = 64 * 1024

#: How deep a style may nest, in YAML text and in a mapping. A style nests five
#: levels at most (``headings.1.size``); the limit is what keeps the cost of
#: reading one linear in its size, since PyYAML's scanner is quadratic in the
#: nesting depth of what it reads.
MAX_STYLE_DEPTH = 32

#: How an untrusted style is named in a message, instead of its own name (which
#: is source text) or a path.
_UNTRUSTED_SOURCE = "'untrusted style'"


class _StyleLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` that refuses a text nested more than
    :data:`MAX_STYLE_DEPTH` levels, as the scanner reads it: before any
    structure is built, and before the quadratic cost of a deep one is paid."""

    def _check_depth(self) -> None:
        if self.flow_level + len(self.indents) >= MAX_STYLE_DEPTH:
            raise yaml.scanner.ScannerError(None, None, "the text is nested too deeply", self.get_mark())

    def fetch_flow_collection_start(self, TokenClass: Any) -> None:  # noqa: N803
        self._check_depth()
        super().fetch_flow_collection_start(TokenClass)

    def add_indent(self, column: int) -> bool:
        if self.indent < column:
            self._check_depth()
        return super().add_indent(column)


def _load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_StyleLoader)  # noqa: S506 - a SafeLoader


class _TooDeep(Exception):
    """A mapping nested more than :data:`MAX_STYLE_DEPTH` levels."""


def builtin_styles() -> list[str]:
    """Names of the style templates shipped with the package."""
    folder = resources.files(__package__).joinpath("builtin")
    return sorted(item.name[: -len(".yaml")] for item in folder.iterdir() if item.name.endswith(".yaml"))


def load_style(
    style: str | Path | Style | Mapping[str, Any] | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
    trusted: bool = True,
) -> Style:
    """The validated :class:`Style` for *style* — a built-in name, a path to
    a YAML file, a :class:`Style`, a mapping, or YAML text (a string with a
    line break) — with *overrides* applied.

    *overrides* maps dotted keys to values, e.g.
    ``{"numbering.scheme": "mixed", "locale": "cs-CZ"}``; nested mappings are
    accepted too.

    Always: a value written into the stylesheet may not contain a comment
    delimiter, ``</``, a backslash, a control character or a line break, and
    ``html.extra_css`` may not contain ``</style``.

    With ``trusted=False`` the style is somebody else's. It is a built-in
    name, a :class:`Style`, a mapping, or YAML text of at most 64 KiB without
    aliases, never a file; it may extend only built-in styles; every value that
    reaches the stylesheet (fonts, sizes, colours, margins, page size) must
    match a strict grammar (:data:`~.model.CSS_KINDS`), also in a
    :class:`Style` and in *overrides*; and ``html.extra_css`` must be empty.
    A message names the setting and the problem and never quotes the style or
    a path.
    """
    try:
        return _load(style, overrides, trusted)
    except (_TooDeep, RecursionError):  # the second only as a last resort
        raise StyleError(_UNTRUSTED_SOURCE if not trusted else "'(style)'",
                         ["the style is nested too deeply"]) from None


def _load(style: Any, overrides: Mapping[str, Any] | None, trusted: bool) -> Style:
    if isinstance(style, Style):
        data = style_to_dict(style)
        source = f"'{style.name}'" if trusted else _UNTRUSTED_SOURCE
    elif isinstance(style, Mapping) or (isinstance(style, str) and ("\n" in style or "\r" in style)):
        source = "'(style)'" if trusted else _UNTRUSTED_SOURCE
        raw = _parse(style, source, trusted) if isinstance(style, str) else _check_raw(_copy(style), source, trusted)
        data, _ = _extend(raw, source, None, f"'(style {id(raw)})'", (), trusted)
    else:
        data, source = _load_chain(style if style is not None else "default", seen=(), trusted=trusted)
    if overrides:
        expanded = _expand_dotted(overrides, trusted)
        _refuse_meta_overrides(expanded)
        data = _merge(data, expanded)
    return _build(data, source, trusted)


def style_to_dict(style: Style) -> dict[str, Any]:
    """*style* as plain data, in the shape a YAML style template is written."""
    return _plain(style)


def dump_style(style: Style) -> str:
    """*style* as a complete, self-contained YAML style template.

    Every setting is written out, so the template extends nothing: ``extends``
    names a file relative to the one that holds it, which a dump written
    elsewhere (``--print-style > house.yaml``) could not find again."""
    data = style_to_dict(style)
    data.pop("extends", None)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_chain(
    spec: str | Path, *, seen: tuple[tuple[str, str], ...], relative_to: Path | None = None, trusted: bool = True
) -> tuple[dict, str]:
    """The merged raw mapping for *spec* and every template it extends.

    *seen* holds the (identity, label) of each template on the chain so far.
    A file's identity is its resolved path, so ``../dir/a.yaml`` and
    ``a.yaml`` — or a symbolic link — are one template and a cycle through
    them is found."""
    raw, source, folder, identity = _read(spec, relative_to, trusted)
    return _extend(raw, source, folder, identity, seen, trusted)


def _extend(raw: dict, source: str, folder: Path | None, identity: str,
            seen: tuple[tuple[str, str], ...], trusted: bool) -> tuple[dict, str]:
    """*raw* merged onto the template it extends (``default`` when it names none)."""
    if any(identity == earlier for earlier, _ in seen):
        chain = " -> ".join((*(label for _, label in seen), source))
        raise StyleError(source, [f"extends: circular chain {chain}"])
    here = (*seen, (identity, source))
    parent = raw.get("extends")
    if parent is None:
        if source == "'default'":
            # Start from every built-in default, so that a partial mapping —
            # one heading level's alignment, say — merges field by field.
            return _merge(style_to_dict(Style()), raw), source
        base, _ = _load_chain("default", seen=here, trusted=trusted)
    elif isinstance(parent, str) and parent:
        if not trusted and not _is_builtin_name(parent):
            raise StyleError(source, ["extends: an untrusted style may extend only a built-in style, "
                                      f"by name (built-in: {', '.join(builtin_styles())})"])
        base, _ = _load_chain(parent, seen=here, relative_to=folder, trusted=trusted)
    else:
        raise StyleError(source, ["extends: must be a style name or a path" if trusted
                                  else "extends: must be the name of a built-in style"])
    return _merge(base, raw), source


def _is_builtin_name(name: str) -> bool:
    return name in builtin_styles()


def _read(spec: str | Path, relative_to: Path | None,
          trusted: bool = True) -> tuple[dict, str, Path | None, str]:
    """The raw mapping in *spec*, a label for messages, the folder that
    relative ``extends`` paths are resolved against, and the template's
    identity (its resolved path, or the quoted name of a built-in). An
    untrusted style reads built-in styles only, never a file."""
    name = str(spec)
    is_path = isinstance(spec, Path) or name.endswith((".yaml", ".yml")) or "/" in name or "\\" in name
    if not trusted:
        if not isinstance(spec, str) or is_path or not _is_builtin_name(name):
            known = ", ".join(builtin_styles())
            raise StyleError(_UNTRUSTED_SOURCE, [f"an untrusted style is a built-in style (built-in: {known}), "
                                                 f"a mapping, or YAML text, never a file"])
        is_path = False
    if not is_path:
        if name not in builtin_styles():
            known = ", ".join(builtin_styles())
            raise StyleError(f"'{name}'", [f"no built-in style named '{name}' (built-in: {known}); "
                                           f"to load a file, give a path ending in .yaml"])
        text = resources.files(__package__).joinpath("builtin", f"{name}.yaml").read_text("utf-8")
        return _parse(text, f"'{name}'"), f"'{name}'", None, f"'{name}'"
    path = Path(spec)
    if relative_to is not None and not path.is_absolute():
        path = relative_to / path
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as error:
        raise StyleError(str(path), [f"cannot read the file: {error.strerror or error}"]) from None
    except UnicodeDecodeError as error:
        raise StyleError(str(path), [f"cannot read the file: not valid UTF-8 (byte 0x{error.object[error.start]:02x} "
                                     f"at offset {error.start})"]) from None
    raw = _parse(text, str(path))
    raw.setdefault("name", path.stem)
    return raw, str(path), path.parent, _identity(path)


def _identity(path: Path) -> str:
    """*path* made absolute, with ``..`` and symbolic links followed, so two
    spellings of one file compare equal."""
    try:
        return str(path.resolve())
    except (OSError, RuntimeError):  # a symbolic link loop
        return os.path.abspath(path)


def _parse(text: str, source: str, trusted: bool = True) -> dict:
    if not trusted:
        _check_untrusted_text(text)
    try:
        raw = _load_yaml(text)
    except yaml.YAMLError as error:
        # PyYAML's message quotes the source around the problem, which for a
        # style from elsewhere would show a reader whatever the text holds.
        raise StyleError(source, [f"not valid YAML: {error}" if trusted else _untrusted_yaml_problem(error)]) from None
    return _check_raw({} if raw is None else raw, source, trusted)


def _check_raw(raw: Any, source: str, trusted: bool = True) -> dict:
    """*raw*, a style template as loaded, once it is known to be a mapping of
    the format version this renderer reads."""
    if not isinstance(raw, dict):
        raise StyleError(source, ["a style template must be a YAML mapping"])
    version = raw.get("version", STYLE_FORMAT_VERSION)
    if version != STYLE_FORMAT_VERSION:
        got = f", got {version!r}" if trusted else ""
        raise StyleError(source, [f"version: this renderer reads style format {STYLE_FORMAT_VERSION}{got}"])
    return raw


def _check_untrusted_text(text: str) -> None:
    """Refuse YAML text that is too large or uses aliases. An alias lets a
    small text stand for an enormous structure (the "billion laughs" shape),
    and nothing in a style needs one."""
    if len(text.encode("utf-8", "replace")) > MAX_UNTRUSTED_STYLE_BYTES:
        raise StyleError(_UNTRUSTED_SOURCE, [f"the style text is larger than {MAX_UNTRUSTED_STYLE_BYTES // 1024} KiB"])
    try:
        for event in yaml.parse(text, Loader=_StyleLoader):
            if isinstance(event, yaml.AliasEvent):
                raise StyleError(_UNTRUSTED_SOURCE, [f"YAML aliases are not allowed (line {event.start_mark.line + 1})"])
    except yaml.YAMLError:
        pass  # reported, without the source, by the load that follows


def _untrusted_yaml_problem(error: yaml.YAMLError) -> str:
    """Where a YAML text is wrong, and what, without quoting it. A scanner or
    parser problem names a kind of mistake; any other (an unknown tag, an
    undefined alias, a duplicate anchor) names text from the source, so it
    gives the place only."""
    mark = getattr(error, "problem_mark", None)
    where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else ""
    problem = getattr(error, "problem", None)
    if problem and isinstance(error, (yaml.scanner.ScannerError, yaml.parser.ParserError)):
        # Even there a quoted piece can be the source's (a tag handle).
        problem = re.sub(r"'[^']*'", "'...'", problem)
        return f"not valid YAML: {problem}{where}"
    return f"not valid YAML{where}"


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


def _expand_dotted(overrides: Mapping[str, Any], trusted: bool = True) -> dict:
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
                raise StyleError("overrides", [f"{key}: '{part}' is set to a value and cannot hold keys" if trusted
                                               else "an override is set to a value and cannot hold keys"])
        value = _copy(value)
        if isinstance(value, dict) and isinstance(target.get(last), dict):
            value = _merge(target[last], value)
        target[last] = value
    return expanded


#: Top-level settings that describe the template file, not how a document
#: looks; a job's overrides cannot change them.
_META_KEYS = {
    "version": "the style format version belongs to the template file",
    "extends": "choose the template with --style (or RenderOptions.style) instead",
}


def _refuse_meta_overrides(expanded: Mapping) -> None:
    """Overrides are applied after the ``extends`` chain is resolved and the
    version checked, so an override of either would be silently ignored — or,
    for ``--print-style``, written into a style that no longer loads."""
    problems = [f"{key}: cannot be overridden; {reason}" for key, reason in _META_KEYS.items() if key in expanded]
    if problems:
        raise StyleError("overrides", problems)


def _copy(value: Any, depth: int = 0) -> Any:
    """A deep copy of *value* with every mapping made a plain dict. A value
    nested more than :data:`MAX_STYLE_DEPTH` levels raises :class:`_TooDeep`."""
    if depth > MAX_STYLE_DEPTH:
        raise _TooDeep
    if isinstance(value, Mapping):
        return {str(key): _copy(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy(item, depth + 1) for item in value)
    return copy.deepcopy(value)


def parse_override(key: str, text: str) -> Any:
    """The value for setting *key* written as *text* on a command line.

    Text settings take *text* as written, so ``[_____]``, ``{designation}``
    or ``C:\\docs`` stay text. A value wrapped in one pair of matching quotes
    that does not contain that quote character (``"none"``, ``' | '``) is
    unquoted — nothing else is interpreted, no escapes and no comments. To
    keep quotation marks around a value, wrap it in the other kind
    (``'"{designation}"'``). Other settings read it as YAML, so that
    ``true``, ``2``, or ``[...]`` mean what they say. ``null`` clears a
    setting that may be unset, such as ``locale``. A value that is not valid
    YAML raises :class:`StyleError`, naming *key* and the YAML problem.
    """
    hint = _hint_for(key.split("."))
    if hint is not None and _is_text(hint):
        stripped = text.strip()
        if type(None) in get_args(hint) and stripped in ("null", "~"):
            return None
        quote = stripped[:1]
        if len(stripped) >= 2 and quote in "'\"" and stripped[-1] == quote and quote not in stripped[1:-1]:
            return stripped[1:-1]
        return text
    try:
        return _load_yaml(text) if text.strip() else ""
    except yaml.YAMLError as error:
        raise StyleError("overrides", [f"{key}: the value is not valid YAML: {_yaml_problem(error)}"]) from None


def _yaml_problem(error: yaml.YAMLError) -> str:
    """What is wrong in a YAML text, on one line: the problem and where it is."""
    problem = getattr(error, "problem", None)
    if not problem:
        return " ".join(str(error).split())
    context = getattr(error, "context", None)
    mark = getattr(error, "problem_mark", None)
    where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else ""
    return f"{context}, {problem}{where}" if context else f"{problem}{where}"


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


#: ``{name}`` as the resolver's ``fill`` and label substitution read it.
_FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _build(data: Mapping, source: str, trusted: bool = True) -> Style:
    problems: list[str] = []
    style = _convert(data, Style, "", problems, trusted=trusted)
    problems.extend(_template_problems(style, trusted))
    if problems:
        raise StyleError(source, problems)
    return style


def _template_problems(style: Style, trusted: bool = True) -> list[str]:
    """What is wrong with the ``{field}`` names in the style's text templates
    (:func:`~.model.template_settings`): an identifier-shaped field the
    setting does not accept (a typo would be shown literally), or a required
    field it leaves out (``labels.condition`` without ``{condition}`` would
    hide the condition, which §15.8 requires to be shown). An empty template
    is exempt from the required fields where the resolver falls back to the
    bare field for it (``labels.condition``): it already shows the condition.
    Where there is no such fallback, an empty template lacks its fields.
    :func:`~.model.template_settings` lists only the settings that substitute
    fields, and drops the required fields of one the style does not use. An
    untrusted style's own field name is not quoted."""
    problems = []
    for key, template, fields in template_settings(style):
        found = set(_FIELD_RE.findall(template))
        accepted = ", ".join(f"{{{name}}}" for name in sorted(fields.accepted))
        for name in sorted(found - fields.accepted):
            takes = f"accepts {accepted}" if accepted else "takes no fields"
            unknown = f"{{{name}}}" if trusted else "a {field}"
            problems.append(f"{key}: unknown field {unknown}; this setting {takes}")
        if fields.empty_falls_back and not template:
            continue
        for name in sorted(fields.required - found):
            problems.append(f"{key}: must contain {{{name}}}" + (f" — {fields.why}" if fields.why else ""))
    return problems


def _got(value: Any, trusted: bool) -> str:
    """`` (got 3)`` for a message about a value — nothing for an untrusted
    style, whose text a message does not quote."""
    return f" (got {value!r})" if trusted else ""


def _convert(value: Any, hint: Any, path: str, problems: list[str], *, trusted: bool = True,
             css: str | None = None) -> Any:
    """*value* as *hint*, appending each problem to *problems*. *css* is the
    kind of a text setting that reaches the stylesheet (:func:`~.model.css_field`),
    whose value :func:`~.model.css_problem` checks; *trusted* is False for a
    style from someone else, which is held to the strict grammars and never
    quoted."""
    label = path or "(root)"
    origin = get_origin(hint)
    if dataclasses.is_dataclass(hint):
        return _convert_dataclass(value, hint, path, problems, trusted=trusted)
    if origin is Literal:
        allowed = get_args(hint)
        # By type too: True == 1 and 2.0 == 2, but neither is a level.
        if not any(value == option and type(value) is type(option) for option in allowed):
            problems.append(f"{label}: must be one of {', '.join(map(str, allowed))}{_got(value, trusted)}")
            return allowed[0]
        return value
    if origin in (Union, types.UnionType):
        options = [arg for arg in get_args(hint) if arg is not type(None)]
        if value is None and type(None) in get_args(hint):
            return None
        return _convert(value, options[0], path, problems, trusted=trusted, css=css)
    if origin is tuple:
        item_hint = get_args(hint)[0]
        if not isinstance(value, (list, tuple)):
            problems.append(f"{label}: must be a list")
            return ()
        return tuple(_convert(item, item_hint, f"{path}[{index}]", problems, trusted=trusted)
                     for index, item in enumerate(value))
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
                    problems.append(f"{path}.{key}: key must be a number" if trusted
                                    else f"{path}: a key must be a number")
                    continue
                if not 1 <= key <= 5:
                    problems.append(f"{path}.{key}: heading levels are 1 to 5 (§4.1)")
                    continue
            converted[key] = _convert(item, item_hint, f"{path}.{key}", problems, trusted=trusted)
        return converted
    if hint is bool:
        if not isinstance(value, bool):
            problems.append(f"{label}: must be true or false{_got(value, trusted)}")
            return False
        return value
    if hint is int:
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append(f"{label}: must be a whole number{_got(value, trusted)}")
            return 0
        return value
    if hint is str:
        # YAML reads 700 or 1.5 as numbers; they are fine as text here.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        elif not isinstance(value, str):
            problems.append(f"{label}: must be text{_got(value, trusted)}")
            return ""
        if css is not None and (problem := css_problem(value, css, trusted=trusted)):
            problems.append(f"{label}: {problem}")
            return ""
        return value
    raise TypeError(f"unsupported style field type {hint!r} at {label}")  # a bug in model.py


def _convert_dataclass(value: Any, cls: type, path: str, problems: list[str], *, trusted: bool = True) -> Any:
    label = path or "(root)"
    if isinstance(value, cls):
        # Checked like data, not taken as built: a settings object handed in as
        # an override would otherwise skip every check below.
        value = _plain(value)
    if cls is model.Numbering and isinstance(value, Mapping) and isinstance(value.get("levels"), (list, tuple)) \
            and len(value["levels"]) > 5:
        problems.append(f"{path}.levels: at most 5 formats, one per heading level (§4.1); got {len(value['levels'])}")
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
            if not trusted:
                # The key is the style's own text: say where, and what is known instead.
                hint = f"did you mean '{suggestion}'?" if suggestion else f"known: {', '.join(known)}"
                problems.append(f"{path or '(root)'}: unknown key ({hint})")
            else:
                problems.append(f"{where}: unknown key" + (f" (did you mean '{suggestion}'?)" if suggestion else ""))
            continue
        values[key] = _convert(item, hints[key], where, problems, trusted=trusted,
                               css=known[key].metadata.get("css"))
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
