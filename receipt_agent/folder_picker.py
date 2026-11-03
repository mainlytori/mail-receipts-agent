"""Lets the user choose where the expenses table is saved."""

from __future__ import annotations

from pathlib import Path

from dotenv import set_key

ENV_FILE = ".env"


def choose_folder() -> Path:
    """Opens a 'choose a folder' window, or asks in the terminal if no window can open."""
    print("\nChoose the folder where your expenses table should be saved...")
    folder = ""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)  # show the window in front of everything
        folder = filedialog.askdirectory(
            title="Where should the expenses table be saved?", mustexist=True
        )
        root.destroy()
    except Exception:  # no screen (e.g. a server) or tkinter missing
        pass
    while not folder:
        typed = input("Type the full path of a folder (e.g. C:\\Users\\me\\Documents): ").strip()
        folder = typed.strip('"')
        if folder and not Path(folder).expanduser().is_dir():
            print("That folder doesn't exist, try again.")
            folder = ""
    path = Path(folder).expanduser().resolve()
    if not Path(ENV_FILE).exists():
        Path(ENV_FILE).touch()
    set_key(ENV_FILE, "OUTPUT_FOLDER", str(path), quote_mode="never")
    print(f"Saved. Your table will be in: {path}\n")
    return path
