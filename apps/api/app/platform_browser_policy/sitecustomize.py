"""Fail closed before any vendored account code if policy cannot be installed."""
import os

try:
    from browser_policy import install
    install()
except BaseException:
    # Python normally suppresses sitecustomize failures and runs the script.
    # Account execution must instead stop before reaching platform operations.
    os._exit(78)
