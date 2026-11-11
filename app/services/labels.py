"""How a class + section is written for people ("Class 1 — A").

A class that is not split into sections (classes.has_sections = 0, migration
0021) still has one section row, with an empty name: it is written as just
the class ("Class 5").
"""
from __future__ import annotations


def class_section(class_name: str | None, section_name: str | None, sep: str = " — ") -> str:
    if class_name and section_name:
        return f"{class_name}{sep}{section_name}"
    return class_name or section_name or ""
