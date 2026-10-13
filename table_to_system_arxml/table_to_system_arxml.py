#!/usr/bin/env python

from pathlib import Path

from table_to_system_arxml.cli import main


if __name__ == "__main__":
    raise SystemExit(main(default_config=Path(__file__).resolve().with_name("config.json")))
