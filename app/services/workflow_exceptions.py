from __future__ import annotations


class WorkflowNotFoundError(Exception):
    """Requested operational projection does not exist."""


class WorkflowConflictError(Exception):
    """Requested operation conflicts with the current workflow state."""


class WorkflowValidationError(Exception):
    """Requested operation is structurally invalid for the current state."""
