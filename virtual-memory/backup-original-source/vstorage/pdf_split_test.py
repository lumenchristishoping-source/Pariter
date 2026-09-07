#!/usr/bin/env python3
"""
Instead of compressing the whole PDF as one blob (which gets only 1.4x because
binary overhead kills it), SPLIT it into distinct pieces first, then run each
piece through the virtual storage process separately.

A PDF naturally splits into:
  - Text content per page (compresses very well)
  - Binary structure/format overhead (less compressible, but isolatable)
  - Metadata (tiny, compresses well)

Each piece falls through its own box chain. The system holds N small falling
pieces instead of one barely-compressed blob.
"""
import os, lzma, hashlib, json
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

os.chdir("/home/claude/vstorage")

def rss_mb():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1])/1024.0

# ---- The PDF we already made ----
pdf_path = "/home/claude/vstorage/unstructured_real.pdf"
pdf_bytes = open(pdf_path,"rb").read()
orig_hash = hashlib.sha256(pdf_bytes).hexdigest()
pdf_size = len(pdf_bytes)

print("="*64)
print("  SPLIT APPROACH: PDF broken into pieces, each falls separately")
print("="*64)
print(f"  PDF: {pdf_size:,} bytes ({pdf_size/1024:.1f} KB)")
print()

# ---- SPLIT the PDF into pieces ----
reader = PdfReader(pdf_path)
pieces = {}

# Piece 1: text content per page (most compressible)
page_texts = []
for i, page in enumerate(reader.pages):
    text = page.extract_text() or ""
    page_texts.append(text)
pieces["text_content"] = "\n---PAGE---\n".join(page_texts).encode()

# Piece 2: metadata (tiny, structured)
meta = reader.metadata or {}
meta_dict = {k: str(v) for k,v in meta.items()}
meta_dict["pages"] = len(reader.pages)
meta_dict["orig_hash"] = orig_hash  # so we can verify later
pieces["metadata"] = json.dumps(meta_dict, indent=2).encode()

# Piece 3: raw binary structure (everything that's NOT the text -- the PDF
# format overhead, fonts, xref tables etc. We get this as what's left)
# We store the full raw PDF bytes as the "binary structure" piece -- this is
# what lets us reconstruct exactly, but it's the hard-to-compress part
pieces["binary_structure"] = pdf_bytes  # the part that doesn't compress well

print("PIECES extracted from PDF:")
print(f"{'piece':<20} {'raw size':>10} {'compressed':>12} {'ratio':>7}")
print("-"*52)

compressed_pieces = {}
total_raw = 0
total_compressed = 0

for name, data in pieces.items():
    compressed = lzma.compress(data, preset=9)
    ratio = len(data)/len(compressed) if len(compressed) > 0 else 1
    compressed_pieces[name] = compressed
    total_raw += len(data)
    total_compressed += len(compressed)
    print(f"  {name:<18} {len(data):>10,} {len(compressed):>12,} {ratio:>6.2f}x")

print("-"*52)
print(f"  {'TOTAL':<18} {total_raw:>10,} {total_compressed:>12,} {total_raw/total_compressed:>6.2f}x")
print()
print(f"  Compare: whole PDF compressed = 13,648 bytes (1.42x)")
print(f"  Split+compress total          = {total_compressed:,} bytes")

# ---- Each piece falls through its own box chain ----
print()
print("EACH PIECE falls through its own box chain (64KB boxes):")
BOX = 64*1024
for name, compressed in compressed_pieces.items():
    n_boxes = max(1, len(compressed)//BOX + 1)
    print(f"  {name}: {len(compressed):,} bytes → {n_boxes} boxes falling")

# ---- RETRIEVAL: rebuild the full PDF from pieces ----
print()
print("RETRIEVAL TEST: rebuild full PDF from falling pieces...")

# catch the binary_structure piece from its fall -- that's the full PDF
rebuilt_pdf = lzma.decompress(compressed_pieces["binary_structure"])
rebuilt_hash = hashlib.sha256(rebuilt_pdf).hexdigest()
exact = rebuilt_hash == orig_hash
print(f"  Full PDF rebuilt byte-perfect: {exact}")

# but also show: for text-only retrieval (like PII scanning), only the
# text piece needs to be caught -- not the whole PDF
rebuilt_text = lzma.decompress(compressed_pieces["text_content"]).decode()
print(f"  Text-only retrieval works too: {len(rebuilt_text):,} chars extracted")
print(f"  Text piece alone: {len(compressed_pieces['text_content']):,} bytes")
print(f"  (for PII scanning, only this piece needs to be caught -- not the full PDF)")

print()
print("="*64)
print("  HONEST RESULT")
print("="*64)
print(f"  Splitting reveals the truth: the PDF's text compresses well,")
print(f"  but the binary_structure piece (the full PDF) still costs {len(compressed_pieces['binary_structure']):,} bytes.")
print(f"  The real win from splitting: you can hold/process ONLY the text")
print(f"  piece for most tasks (PII scanning, AI processing, search) --")
print(f"  {len(compressed_pieces['text_content']):,} bytes instead of {pdf_size:,} bytes -- without touching the")
print(f"  full PDF until you actually need to reconstruct it exactly.")
print()
io = open("/proc/self/io").read()
wb = int([l for l in io.splitlines() if "write_bytes" in l][0].split()[1])
print(f"  Disk writes: {wb} bytes (ROM used: {'YES' if wb>0 else 'NO'})")
