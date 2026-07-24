import dataclasses
import inspect
import re
from pathlib import Path

import rot

_TYPE_ALIASES = {"ProgressCallback", "StageProgressCallback"}
_MARKER = re.compile(r"<!-- rot-api:([A-Za-z_][A-Za-z0-9_]*) -->")


def _mentions(doc: str, name: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", doc) is not None


def _assert_parameters_documented(callable_object: object, label: str) -> None:
    doc = inspect.getdoc(callable_object) or ""
    for parameter in inspect.signature(callable_object).parameters.values():
        if parameter.name in {"self", "cls"}:
            continue
        assert _mentions(doc, parameter.name), f"{label} does not document {parameter.name}"


def test_every_export_has_inline_documentation() -> None:
    for name in rot.__all__:
        exported = getattr(rot, name)
        if name in _TYPE_ALIASES:
            continue
        assert inspect.getdoc(exported), f"rot.{name} has no docstring"
        if inspect.isroutine(exported):
            _assert_parameters_documented(exported, f"rot.{name}")
        if not inspect.isclass(exported):
            continue
        if dataclasses.is_dataclass(exported):
            class_doc = inspect.getdoc(exported) or ""
            for field in dataclasses.fields(exported):
                if field.init and not field.name.startswith("_"):
                    assert _mentions(class_doc, field.name), (
                        f"rot.{name} does not document field {field.name}"
                    )
        constructor = exported.__dict__.get("__init__")
        if (
            constructor is not None
            and not dataclasses.is_dataclass(exported)
            and getattr(constructor, "__module__", "").startswith("rot")
        ):
            class_doc = inspect.getdoc(exported) or ""
            for parameter in inspect.signature(exported).parameters.values():
                if parameter.name not in {"self", "cls"}:
                    assert _mentions(class_doc, parameter.name), (
                        f"rot.{name} does not document constructor parameter {parameter.name}"
                    )
        for member_name, descriptor in exported.__dict__.items():
            if member_name.startswith("_"):
                continue
            member = descriptor
            if isinstance(descriptor, (classmethod, staticmethod)):
                member = descriptor.__func__
            elif isinstance(descriptor, property):
                member = descriptor.fget
            if not inspect.isfunction(member):
                continue
            assert inspect.getdoc(member), f"rot.{name}.{member_name} has no docstring"
            _assert_parameters_documented(member, f"rot.{name}.{member_name}")


def test_every_export_has_a_python_reference_entry() -> None:
    reference = (
        Path(__file__).resolve().parents[1] / "docs" / "reference" / "api.md"
    ).read_text(encoding="utf-8")
    markers = _MARKER.findall(reference)
    assert len(markers) == len(set(markers)), "Python reference contains duplicate API markers"
    assert set(markers) == set(rot.__all__), "Python reference markers do not match rot.__all__"


def _reference_entries(reference: str) -> dict[str, str]:
    matches = list(_MARKER.finditer(reference))
    return {
        match.group(1): reference[
            match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(reference)
        ]
        for index, match in enumerate(matches)
    }


def _assert_reference_parameters(
    callable_object: object,
    entry: str,
    label: str,
    *,
    skip_variadic: bool = False,
) -> None:
    for parameter in inspect.signature(callable_object).parameters.values():
        if parameter.name in {"self", "cls"}:
            continue
        if skip_variadic and parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        assert _mentions(entry, parameter.name), (
            f"{label} parameter {parameter.name} is missing from its Python reference entry"
        )


def test_python_reference_entries_cover_fields_methods_and_parameters() -> None:
    reference = (
        Path(__file__).resolve().parents[1] / "docs" / "reference" / "api.md"
    ).read_text(encoding="utf-8")
    entries = _reference_entries(reference)

    for name in rot.__all__:
        exported = getattr(rot, name)
        entry = entries[name]
        assert re.search(rf"^### .*{re.escape(name)}", entry, re.MULTILINE), (
            f"rot.{name} has no heading in its Python reference entry"
        )
        if name in _TYPE_ALIASES:
            continue
        if inspect.isroutine(exported):
            _assert_reference_parameters(exported, entry, f"rot.{name}")
        if not inspect.isclass(exported):
            continue
        if dataclasses.is_dataclass(exported):
            for field in dataclasses.fields(exported):
                if field.init and not field.name.startswith("_"):
                    assert _mentions(entry, field.name), (
                        f"rot.{name} field {field.name} is missing from its Python reference entry"
                    )
        constructor = exported.__dict__.get("__init__")
        if (
            constructor is not None
            and not dataclasses.is_dataclass(exported)
            and getattr(constructor, "__module__", "").startswith("rot")
        ):
            _assert_reference_parameters(exported, entry, f"rot.{name}")
        for member_name, descriptor in exported.__dict__.items():
            if member_name.startswith("_"):
                continue
            member = descriptor
            if isinstance(descriptor, (classmethod, staticmethod)):
                member = descriptor.__func__
            elif isinstance(descriptor, property):
                member = descriptor.fget
            if not inspect.isfunction(member):
                continue
            assert _mentions(entry, member_name), (
                f"rot.{name}.{member_name} is missing from its Python reference entry"
            )
            _assert_reference_parameters(
                member,
                entry,
                f"rot.{name}.{member_name}",
                skip_variadic=True,
            )
