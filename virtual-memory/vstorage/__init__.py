from .secure_system import SecureVirtualStorage
from .system import VirtualStorage

# SecureVirtualStorage is the real pipeline - encryption, watchdog,
# distributed trust, streaming both ways (see ARCHITECTURE.md /
# HANDBOOK.md). VirtualStorage is the original, unhardened prototype -
# kept importable since some standalone experiments (secrets_api.py,
# demo.py) still build on it directly, not because it's what a new
# caller should reach for.
__all__ = ["SecureVirtualStorage", "VirtualStorage"]
