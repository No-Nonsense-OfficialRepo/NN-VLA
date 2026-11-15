"""Match NNLL's Qt runtime initialization before importing PySide6."""
import os

os.environ["PYSIDE6_OPTION_LAZY"] = "0"
