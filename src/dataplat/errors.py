"""Exception types. Every refusal the platform makes raises one of these with an actionable message."""


class DataplatError(Exception):
    """Base class for platform errors."""


class ConfigError(DataplatError):
    """Missing or invalid configuration (env vars, pyproject [tool.dataplat])."""


class IsolationError(DataplatError):
    """A worktree/namespace boundary would be crossed (e.g. a worktree targeting the main lake)."""


class ContractError(DataplatError):
    """A frame or table does not match its TableContract."""


class LakeError(DataplatError):
    """A lake operation was refused or failed (zero-row partition, wrong partition, conflict)."""


class PartitionExistsError(LakeError):
    """Registering files into a partition that already holds rows (would double-count)."""


class ParityError(DataplatError):
    """A derived artifact (graph) disagrees with the lake on a canary question; nothing was published."""
