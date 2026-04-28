from __future__ import annotations


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def dedupe_articles(values: list[str]) -> list[str]:
    return dedupe_strings(values)
