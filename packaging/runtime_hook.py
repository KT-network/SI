"""Provide a CA bundle when the executable runs without a Python installation."""

import os
import sys

import certifi


os.environ.setdefault("SSL_CERT_FILE", certifi.where())

if "--smoke-test" in sys.argv:
    import ssl

    ssl.create_default_context(cafile=certifi.where())
