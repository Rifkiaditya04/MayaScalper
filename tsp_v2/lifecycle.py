"""Lifecycle scaffold for TSP V2."""

from __future__ import annotations

from .models import LifecycleState


def evaluate_lifecycle(state: LifecycleState) -> LifecycleState:
    raise NotImplementedError("PATCH-000 scaffold only: lifecycle not implemented yet.")
