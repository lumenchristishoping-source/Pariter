#!/usr/bin/env bash
# Sets up and boots a single Firecracker microVM: a "container" with a real
# vCPU boundary (no raw host CPU access), ephemeral by design.
#
# PREREQUISITE — this will NOT run in a sandbox like the one that wrote it:
#   - Linux host with /dev/kvm present
#   - CPU virtualization exposed: `grep -oE 'vmx|svm' /proc/cpuinfo` must
#     print something. If it prints nothing, this cannot run there either -
#     you need a host with hardware virtualization actually passed through
#     (a bare-metal cloud instance, a VM with nested virtualization enabled,
#     or a normal machine you control).
#
# Usage: sudo ./setup.sh

set -euo pipefail

WORKDIR="$(pwd)/fc-vm"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "== Checking prerequisites =="
if [ ! -e /dev/kvm ]; then
  echo "ERROR: /dev/kvm not found. This host cannot run Firecracker." >&2
  exit 1
fi
if ! grep -qE 'vmx|svm' /proc/cpuinfo; then
  echo "ERROR: no vmx/svm CPU flags. Hardware virtualization isn't exposed here." >&2
  exit 1
fi
echo "KVM present, virtualization flags present. Proceeding."

echo "== Fetching Firecracker binary =="
ARCH="$(uname -m)"
RELEASE_URL="https://github.com/firecracker-microvm/firecracker/releases/latest/download/firecracker-${ARCH}"
curl -fsSL -o firecracker "$RELEASE_URL" || {
  echo "Direct binary fetch failed - check the Firecracker releases page for the current asset name:" >&2
  echo "https://github.com/firecracker-microvm/firecracker/releases" >&2
  exit 1
}
chmod +x firecracker

echo "== Fetching a minimal guest kernel + rootfs (CI test images) =="
# These are the small CI test artifacts the Firecracker project publishes for
# exactly this purpose - swap for your own kernel/rootfs for anything real.
CI_BUCKET="https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/v1.10"
curl -fsSL -o vmlinux "${CI_BUCKET}/${ARCH}/vmlinux-5.10.bin" || echo "Kernel fetch failed - fetch your own guest kernel and place it at ./vmlinux"
curl -fsSL -o rootfs.ext4 "${CI_BUCKET}/${ARCH}/ubuntu-22.04.ext4" || echo "Rootfs fetch failed - build/fetch your own rootfs and place it at ./rootfs.ext4"

echo "== Writing VM config =="
cat > vm-config.json <<'EOF'
{
  "boot-source": {
    "kernel_image_path": "./vmlinux",
    "boot_args": "console=ttyS0 reboot=k panic=1 pci=off"
  },
  "drives": [
    {
      "drive_id": "rootfs",
      "path_on_host": "./rootfs.ext4",
      "is_root_device": true,
      "is_read_only": false
    }
  ],
  "machine-config": {
    "vcpu_count": 1,
    "mem_size_mib": 128
  }
}
EOF

echo "== Ready. To boot the microVM: =="
cat <<'EOF'

  rm -f /tmp/firecracker.socket
  ./firecracker --api-sock /tmp/firecracker.socket --config-file ./vm-config.json

That process IS the container: one vCPU, 128MB RAM, no disk beyond the
rootfs image, no raw access to the host's physical CPU (KVM mediates
every instruction trap). Kill the firecracker process and the whole
thing is gone - nothing persists unless you explicitly copied something
out first. Run this script again for a fresh one.

EOF
