# Virtual Memory — a software-only, RAM-based storage system

Standalone project. Not related to Pariter (shelved) — this folder name
is a provisional placeholder; rename freely.

## The concept, verbatim

> well we need to find a way to make a virtual memory one that doesnt rely on disk space and anything maybe a little of Ram though so what do you think?

> this has nothing to do with paritar that is dead leave it and forget about it

> i said we are making a virtual storage made fully of software/code which allows you store things my first thing was to isolate the environment which we will create from the cpu and other things so one can say the environment or container is in a vacuum but the container is like a box that breaks and since its in ram it is killed and forms again it just like a never ending collapsing container it collapses and forms again

## Plain restatement

A storage/execution environment that:
1. Is built entirely in software (no dependency on physical disk).
2. Is isolated from the host CPU and the rest of the system — a
   self-contained "vacuum."
3. Lives in RAM, not disk.
4. Has a repeating lifecycle: it's created, does its job, is destroyed
   ("collapses"), and a fresh one is created again — an endless cycle,
   not a single long-lived instance.

## This already exists — Firecracker microVMs

What's described here maps closely onto **Firecracker**, the
microVM technology AWS built to run Lambda, now widely used to
sandbox AI-generated code execution specifically:

- **Isolation "in a vacuum"**: uses KVM (Linux's virtualization layer)
  to give each instance its own virtual CPU and memory — real
  hardware-level isolation from the host and from every other
  instance, not just a process-level boundary.
- **"Killed and forms again"**: each execution gets its own microVM,
  destroyed the moment it's done — nothing leaks between runs, nothing
  persists if something misbehaves. Spin up, run, destroy, repeat.
- **"A little RAM"**: boots in ~125ms (as low as 28ms using snapshots),
  under 5MB of memory overhead per instance.

## The three real tiers (isolation strength vs. speed/footprint)

| Option | Isolation | Startup | Best for |
|---|---|---|---|
| **Firecracker** (microVM) | Strongest — real VM boundary via KVM | ~125ms (28ms w/ snapshots) | A full environment — real filesystem, real processes |
| **gVisor** | Middle — user-space kernel intercepts syscalls; writable state lives in RAM (tmpfs), wiped via one call | Faster than a VM, 20–50% overhead vs. bare containers | Container-like isolation without full VM weight |
| **WebAssembly (WASM)** | Lightest — bounds-checked linear memory, denies filesystem/network by default | Microseconds | Pure computation, not full OS-like behavior |

**Recommendation**: Firecracker is the closest match to what's
described — it's built specifically for "isolated, ephemeral, collapses
and reforms," it's already the industry's default answer to sandboxing
AI-generated code, and (unlike WASM) it supports a real filesystem and
real processes, which matters if this is meant to behave like a genuine
storage environment rather than a narrow compute sandbox.

## Sources

- [Firecracker — official](https://firecracker-microvm.github.io/)
- [How to sandbox AI agents in 2026: Firecracker, gVisor, runtimes & isolation strategies](https://manveerc.substack.com/p/ai-agent-sandboxing-guide)
- [How I built sandboxes that boot in 28ms using Firecracker snapshots](https://dev.to/adwitiya/how-i-built-sandboxes-that-boot-in-28ms-using-firecracker-snapshots-i0k)
- [Firecracker, gVisor, Containers, and WebAssembly — comparing isolation technologies for AI agents](https://www.softwareseni.com/firecracker-gvisor-containers-and-webassembly-comparing-isolation-technologies-for-ai-agents/)

## Open questions

- What actually gets stored in this environment, and for how long
  before it "collapses"? (Is data handed off somewhere durable before
  each collapse, or is loss-on-collapse acceptable/intended?)
- Is this meant to run on a server (cloud host with KVM support) or
  on-device? Firecracker specifically requires KVM, which rules out
  running it directly on iOS/Android — worth confirming the target
  environment before committing to it.

## Live findings from this session's sandbox

Confirmed requirement: **no raw/direct host CPU access** — the
container must run on a real virtual CPU boundary (a hypervisor
mediating every instruction trap), not just syscall interception.

- **This exact sandbox cannot provide that.** `/dev/kvm` doesn't
  exist, and — more fundamentally — `/proc/cpuinfo` shows no
  `vmx`/`svm` flags at all, meaning hardware virtualization instructions
  aren't exposed to this container in the first place. Not a
  permissions issue; nested virtualization isn't passed through.
- **Tested gVisor (`runsc`) here as a possible substitute — it does
  NOT satisfy this requirement.** Its ptrace platform (the only one
  that works without KVM) intercepts syscalls, but application code
  still executes directly on the real physical CPU. Proved the
  collapse-and-reform lifecycle works (`runsc do` twice, second
  instance had zero visibility into the first's files) — genuinely
  useful for ephemerality, but not for CPU isolation specifically.
- **What actually satisfies "no raw CPU access"**: Firecracker (or
  gVisor's own KVM platform) — both need `/dev/kvm` and real
  virtualization flags on the host. `firecracker/setup.sh` in this
  folder is the ready-to-run setup (fetches the binary, a minimal
  guest kernel + rootfs, writes the VM config) for a real host that
  has them — a bare-metal cloud instance, a VM with nested
  virtualization enabled, or a personal machine with virtualization
  support. It won't run in a sandbox shaped like this one.
