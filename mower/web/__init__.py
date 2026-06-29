"""FastAPI control UI for the mower.

Run with::

    python -m mower serve --ip 192.168.68.108

Requires the `[web]` extra: ``pip install -e .[web]``.

Binds to 127.0.0.1 by default — anyone with shell on this machine can drive
the mower, but nothing on the LAN can. Pass ``--host 0.0.0.0`` to expose
to the network (no auth in v1 — only do this on a trusted LAN).
"""

from .server import create_app, run

__all__ = ["create_app", "run"]
