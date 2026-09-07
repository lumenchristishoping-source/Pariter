#!/usr/bin/env python3
"""
General document splitter for Virtual Storage.

Every file type splits into the same 3 pieces:
  - CONTENT: the actual information (text, data, values) -- compresses well
  - STRUCTURE: format/encoding overhead -- compresses less
  - METADATA: tiny descriptor (type, size, hash, name) -- always tiny

Each piece falls through its own box chain independently.
Only the piece needed for a task gets caught from the fall.
"""
import os, lzma, hashlib, json, struct

os.chdir("/home/claude/vstorage")

def split_document(path):
    """Split ANY file into content + structure + metadata pieces."""
    raw = open(path,"rb").read()
    name = os.path.basename(path)
    ext = name.rsplit(".",1)[-1].lower() if "." in name else "bin"

    content = b""
    structure = b""

    # detect and split by type
    if ext in ("txt","md","py","c","js","json","csv","log","xml","html"):
        # pure text -- content IS the file, no binary structure
        content = raw
        structure = ext.encode()  # just the format tag, trivial

    elif ext == "pdf":
        # extract text layer as content, keep binary as structure
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(raw))
            texts = [p.extract_text() or "" for p in reader.pages]
            content = "\n---PAGE---\n".join(texts).encode()
        except: content = b""
        structure = raw  # the full PDF binary

    elif ext in ("docx","xlsx","pptx"):
        # these are zip files -- extract text content, keep zip as structure
        import zipfile, io
        content_parts = []
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for name_z in z.namelist():
                    if name_z.endswith(".xml") or name_z.endswith(".rels"):
                        try: content_parts.append(z.read(name_z))
                        except: pass
            content = b"\n".join(content_parts)
        except: content = b""
        structure = raw

    elif ext in ("jpg","jpeg","png","gif","webp","mp4","mp3","zip","gz"):
        # already compressed/binary -- content = structure = raw, no split benefit
        content = b""
        structure = raw

    else:
        # unknown binary -- treat as structure
        content = b""
        structure = raw

    # metadata is always the same regardless of type
    metadata = json.dumps({
        "filename": os.path.basename(path),
        "type": ext,
        "size": len(raw),
        "hash": hashlib.sha256(raw).hexdigest(),
        "content_size": len(content),
    }, indent=2).encode()

    return {"content": content, "structure": structure, "metadata": metadata}, raw

def process_through_storage(pieces):
    """Compress each piece -- this is what falls through boxes."""
    result = {}
    for name, data in pieces.items():
        result[name] = lzma.compress(data, preset=9) if data else b""
    return result

def get_io_writes():
    for line in open("/proc/self/io"):
        if line.startswith("write_bytes"):
            return int(line.split()[1])

# ---- Test across ALL our real files ----
test_files = [
    "medical_records.json",
    "sample_code.c",
    "sample_prose.txt",
    "sample_data.json",
    "unstructured_real.pdf",
    "sample_binary.bin",
]

print("="*72)
print("  GENERAL DOCUMENT SPLITTER — Virtual Storage across all file types")
print("="*72)
print(f"{'file':<24} {'raw':>8} {'content':>10} {'struct':>10} {'meta':>6} {'total held':>12} {'ratio':>7}")
print("-"*72)

io_before = get_io_writes()
grand_raw = grand_held = 0

for fp in test_files:
    if not os.path.exists(fp):
        continue
    pieces, raw = split_document(fp)
    compressed = process_through_storage(pieces)
    raw_size = len(raw)
    held_size = sum(len(v) for v in compressed.values())
    ratio = raw_size/held_size if held_size > 0 else 1
    grand_raw += raw_size; grand_held += held_size
    print(f"  {fp:<22} {raw_size:>8,} {len(compressed['content']):>10,} "
          f"{len(compressed['structure']):>10,} {len(compressed['metadata']):>6,} "
          f"{held_size:>12,} {ratio:>6.1f}x")

print("-"*72)
print(f"  {'TOTAL':<22} {grand_raw:>8,} {'':>10} {'':>10} {'':>6} {grand_held:>12,} {grand_raw/grand_held:>6.1f}x")
io_after = get_io_writes()

print()
print("="*72)
print("  RETRIEVAL: which piece do you catch from the fall per task?")
print("="*72)
print("  PII scanning / AI processing  -> catch CONTENT only (smallest)")
print("  Full document reconstruction  -> catch STRUCTURE piece")
print("  Index / search / catalogue    -> catch METADATA only (trivial)")
print("  Nothing needed right now      -> all pieces keep falling, untouched")
print()
print(f"  Disk writes during this whole run: {io_after-io_before:,} bytes")
print("  (virtual storage parts = 0; pypdf/file reads = the nonzero part)")
