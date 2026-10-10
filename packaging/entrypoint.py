"""Standalone entry point used by PyInstaller on all supported platforms."""

from si.main import main


if __name__ == "__main__":
    raise SystemExit(main())
