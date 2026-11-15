"""Launcher release version shared by the UI, networking and Windows build."""

__version__ = "1.0.11"
__release__ = f"V{__version__}"
# Wire protocol number carried in the signed beacon. Bump only for a breaking
# change to beacon or room messages; see contracts/beacon.md.
__proto__ = 3
