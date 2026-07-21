import dataclasses
import inspect
import re
from pathlib import Path

import rot

_TYPE_ALIASES = {"ProgressCallback", "StageProgressCallback"}


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
    for name in rot.__all__:
        assert f"<!-- rot-api:{name} -->" in reference, f"rot.{name} is missing from API reference"
