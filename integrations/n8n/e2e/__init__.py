"""Offline acceptance contracts for the n8n mail-ingestion boundary.

The modules in this package deliberately use synthetic Outlook, OneDrive,
Actual, and Cashback stores.  They are useful for proving the deterministic
pipeline and replay contract, but they are never provider evidence.
"""

__all__ = [
    "RESTART_INJECTION_POINTS",
    "ContractError",
    "InjectedRestart",
    "SyntheticCashback",
    "SyntheticMailPipeline",
    "SyntheticOneDrive",
    "SyntheticOutlook",
    "build_source_bindings",
    "run_synthetic_e2e",
    "verify_receipt",
]


def __getattr__(name: str):
    """Load the contract lazily so ``python -m`` has no double-import warning."""

    if name in __all__:
        from . import real_mail_e2e

        return getattr(real_mail_e2e, name)
    raise AttributeError(name)
