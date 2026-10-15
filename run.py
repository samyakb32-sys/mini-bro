"""Entry point for the packaged exe (PyInstaller can't run minibro/main.py directly: it uses package imports)."""
from minibro.main import main

if __name__ == "__main__":
    main()
