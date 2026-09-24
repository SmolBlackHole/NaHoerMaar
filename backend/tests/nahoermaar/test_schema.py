# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
)

from nahoermaar.database.schema import Base


def test_schema_assigns_stable_constraint_and_index_names() -> None:
    parent = Table(
        "schema_test_parent",
        Base.metadata,
        Column("id", Integer, primary_key=True),
    )
    child = Table(
        "schema_test_child",
        Base.metadata,
        Column("id", Integer, primary_key=True),
        Column(
            "parent_id", Integer, ForeignKey("schema_test_parent.id"), nullable=False
        ),
        Column("slug", String, nullable=False, index=True),
        Column("rank", Integer, nullable=False),
        UniqueConstraint("slug"),
        CheckConstraint("rank >= 0", name="rank_nonnegative"),
    )
    try:
        unique = next(
            constraint
            for constraint in child.constraints
            if isinstance(constraint, UniqueConstraint)
        )
        check = next(
            constraint
            for constraint in child.constraints
            if isinstance(constraint, CheckConstraint)
        )
        foreign_key = next(iter(child.foreign_key_constraints))
        index = next(iter(child.indexes))

        assert parent.primary_key.name == "pk_schema_test_parent"
        assert child.primary_key.name == "pk_schema_test_child"
        assert unique.name == "uq_schema_test_child_slug"
        assert check.name == "ck_schema_test_child_rank_nonnegative"
        assert foreign_key.name == "fk_schema_test_child_parent_id_schema_test_parent"
        assert index.name == "ix_schema_test_child_slug"
    finally:
        Base.metadata.remove(child)
        Base.metadata.remove(parent)
