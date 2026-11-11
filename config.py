"""Deprecated path: use gift.config. Kept so old imports and commands keep working.

Remove this file once open branches have rebased.
"""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("gift.config", run_name="__main__")
else:
    import importlib

    sys.modules[__name__] = importlib.import_module("gift.config")
