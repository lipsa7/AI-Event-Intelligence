"""
Domain-neutral deterministic deduplication helpers.

This module performs only exact or caller-defined key-based deduplication.

It deliberately does NOT decide whether two domain objects are semantically
equivalent.

Examples of logic that does NOT belong here
-------------------------------------------
- whether two sources originate from the same underlying source;
- whether two articles are independently corroborating;
- whether two events describe the same real-world event;
- whether two entities should resolve to the same canonical entity;
- whether two relationships are semantically equivalent;
- whether two evidence records are materially different;
- whether two insights are redundant.

Those decisions require domain knowledge and belong in their owning
services/agents.

The caller must supply the identity key whenever deduplicating structured
objects.
"""

from __future__ import annotations

from collections.abc import (
    Callable,
    Hashable,
    Iterable,
)
from typing import (
    Literal,
    TypeVar,
)


T = TypeVar(
    "T"
)

K = TypeVar(
    "K",
    bound=Hashable,
)

KeepMode = Literal[
    "first",
    "last",
]


class DeduplicationError(
    ValueError
):
    """
    Raised when deterministic key-based
    deduplication cannot be performed.
    """


def stable_unique(
    values: Iterable[K],
) -> list[K]:
    """
    Return unique hashable values while preserving
    first-seen order.

    This function performs exact equality/hash-based
    deduplication only.

    It does not:
    - normalize strings;
    - compare semantic similarity;
    - infer canonical identity;
    - determine source independence.

    Example
    -------
    Input:

        ["A", "B", "A", "C"]

    Output:

        ["A", "B", "C"]
    """

    output: list[K] = []
    seen: set[K] = set()

    for value in values:
        try:
            duplicate = (
                value in seen
            )

        except TypeError as exc:
            raise DeduplicationError(
                "stable_unique requires "
                "hashable values."
            ) from exc

        if duplicate:
            continue

        seen.add(
            value
        )

        output.append(
            value
        )

    return output


def deduplicate_by_key(
    items: Iterable[T],
    *,
    key: Callable[
        [T],
        K,
    ],
    keep: KeepMode = "first",
) -> list[T]:
    """
    Deduplicate objects using an explicit caller-supplied key.

    Parameters
    ----------
    items:
        Objects to deduplicate.

    key:
        Function returning the exact hashable identity
        key for an object.

        The caller owns the semantic meaning of this key.

        This utility never attempts to determine what makes
        two domain objects equivalent.

    keep:
        "first":
            retain the first object encountered for each key.

        "last":
            retain the last object encountered for each key.

            Output ordering follows the positions of the
            surviving objects in the original input.

    Returns
    -------
    list[T]
        Deterministically deduplicated objects.

    Examples
    --------
    Exact URL-key deduplication may be performed by a caller:

        deduplicate_by_key(
            records,
            key=lambda item: item["canonical_url"],
        )

    That does NOT imply the utility knows whether two
    differently addressed sources represent the same
    underlying publication.

    Raises
    ------
    DeduplicationError
        If `keep` is invalid or the supplied key is
        not hashable.
    """

    if keep not in {
        "first",
        "last",
    }:
        raise DeduplicationError(
            "keep must be either "
            "'first' or 'last'."
        )

    # Materialize once so:
    #
    # 1. generator inputs behave predictably;
    # 2. the key function is evaluated exactly once
    #    per item;
    # 3. keep='last' remains deterministic.
    keyed_items: list[
        tuple[
            T,
            K,
        ]
    ] = []

    for item in items:
        item_key = key(
            item
        )

        try:
            hash(
                item_key
            )

        except TypeError as exc:
            raise DeduplicationError(
                "deduplicate_by_key requires "
                "the key function to return "
                "hashable values."
            ) from exc

        keyed_items.append(
            (
                item,
                item_key,
            )
        )

    if keep == "first":
        output: list[T] = []
        seen: set[K] = set()

        for item, item_key in keyed_items:
            if item_key in seen:
                continue

            seen.add(
                item_key
            )

            output.append(
                item
            )

        return output

    last_index_by_key: dict[
        K,
        int,
    ] = {}

    for index, (
        _item,
        item_key,
    ) in enumerate(
        keyed_items
    ):
        last_index_by_key[
            item_key
        ] = index

    return [
        item
        for index, (
            item,
            item_key,
        )
        in enumerate(
            keyed_items
        )
        if (
            last_index_by_key[
                item_key
            ]
            == index
        )
    ]


__all__ = [
    "DeduplicationError",
    "KeepMode",
    "stable_unique",
    "deduplicate_by_key",
]