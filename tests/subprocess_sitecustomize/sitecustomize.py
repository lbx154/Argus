"""No-op sitecustomize for subprocess CLI tests.

The host conda environment installs a global sitecustomize that imports
vLLM/torch at interpreter startup. These tests exercise argus-skill CLI
subprocess behavior, not host ML package startup, so child processes put this
directory first on PYTHONPATH to make startup deterministic.
"""
