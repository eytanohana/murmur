"""Entry point of the frozen app (PyInstaller runs this file)."""

import multiprocessing

from murmur.__main__ import main

if __name__ == "__main__":
    # Python helper processes (the resource tracker) re-run this executable with
    # `-c <code>`; freeze_support handles that instead of treating it as murmur arguments.
    multiprocessing.freeze_support()
    main()
