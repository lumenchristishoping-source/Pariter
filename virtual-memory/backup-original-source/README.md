# Virtual Storage — Source Package

Built by Drew. September 2026.

## Start here
Read `vstorage/VIRTUAL_STORAGE_HANDBOOK.md` — the complete reference.

## Run the main system
```bash
pip install pypdf reportlab numpy psutil
cd vstorage
python3 combined_full_system.py
```

## Compile C components
```bash
gcc -O2 -o bounce_mover bounce_mover.c
gcc -O2 -o splice_mover splice_mover.c
gcc -O2 -o anonymous_mmap_storage anonymous_mmap_storage.c -lpthread
```
