#!/usr/bin/env python3
"""
FULL combined system: compression + the falling-boxes motion.

- A file is saved: compressed into its broken-down form.
- That broken-down form is then kept FALLING through an unlimited series of
  boxes that break and reform continuously (your box idea).
- At rest, the RAM footprint should track the BOX size + the compressed form,
  not the full file.
- On retrieval, the compressed form is caught from the flow and expanded to
  full size only for that instant.

We measure RAM (RssAnon = real data cost) and disk throughout, in C for a
true low footprint, driven/measured from Python externally.
"""
import subprocess, time, os, sys

def get_mem(pid):
    d = {}
    try:
        for line in open(f"/proc/{pid}/status"):
            for k in ("VmRSS","RssAnon","VmHWM"):
                if line.startswith(k+":"): d[k]=int(line.split()[1])/1024.0
    except FileNotFoundError: return None
    return d

def get_io(pid):
    d={}
    try:
        for line in open(f"/proc/{pid}/io"):
            k,v=line.split(":"); d[k.strip()]=int(v)
    except FileNotFoundError: return None
    return d

os.chdir("/home/claude/vstorage")

# C program: holds a compressed blob, keeps it flowing through small boxes that
# break/reform continuously. Box size is fixed and small; the compressed blob
# flows through in box-sized pieces, so at any instant only box-size is "in a
# box" -- but the compressed blob still must persist to stay retrievable, held
# in a backing region. We measure the honest cost.
c = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/mman.h>
#include <string.h>
#include <pthread.h>
#include <time.h>

#define BOXSZ (65536)   // each box is 64KB -- small and fixed

long blob_size;
unsigned char* blob;          // the compressed form (must persist to be retrievable)
volatile long hops=0;
volatile int stop=0;

// the falling boxes: compressed blob flows through 64KB boxes continuously,
// each box catches a piece, passes it on, breaks; a new box forms. Unlimited.
void* fall(void* arg){
    unsigned char* box = malloc(BOXSZ);   // ONE box at a time (breaks & reforms)
    while(!stop){
        long pos=0;
        while(pos<blob_size && !stop){
            long n = (blob_size-pos)<BOXSZ ? (blob_size-pos) : BOXSZ;
            memcpy(box, blob+pos, n);     // box catches a piece of the blob
            // box "breaks" and a new one forms -- we simulate by freeing+alloc
            free(box);
            box = malloc(BOXSZ);          // new box forms as outermost
            memcpy(blob+pos, box, 0);     // (piece passed on)
            pos += n;
            hops++;
        }
    }
    free(box);
    return NULL;
}

int main(int argc, char** argv){
    blob_size = atol(argv[1]);
    // load the compressed blob from a file passed in (already compressed by python)
    FILE* f = fopen(argv[2],"rb");
    blob = mmap(NULL, blob_size, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    fread(blob,1,blob_size,f);
    fclose(f);

    printf("ready pid=%d blob=%ld box=%d\n", getpid(), blob_size, BOXSZ);
    fflush(stdout);

    pthread_t t;
    pthread_create(&t,NULL,fall,NULL);

    // run, reporting hops so we can see it's continuously falling
    for(int i=0;i<8;i++){
        sleep(1);
        printf("tick hops=%ld\n", hops); fflush(stdout);
    }
    stop=1; pthread_join(t,NULL);
    return 0;
}
'''
open("_fallbox.c","w").write(c)
subprocess.run(["gcc","-O2","-o","_fallbox","_fallbox.c","-lpthread"],
               check=True, stderr=subprocess.DEVNULL)

# Python side: take a real file, compress it, hand the compressed blob to the C program
import lzma, hashlib
with open("medical_records.json","rb") as f:
    original = f.read()
full_size = len(original)
compressed = lzma.compress(original, preset=9)
with open("_blob.bin","wb") as f:
    f.write(compressed)

print("="*62)
print("  COMPRESSION + FALLING BOXES combined")
print("="*62)
print(f"  Original file: {full_size:,} bytes ({full_size/1024/1024:.2f} MB)")
print(f"  Compressed blob (what flows through boxes): {len(compressed):,} bytes")
print(f"  Box size (fixed, small): 64 KB")
print()

proc = subprocess.Popen(["./_fallbox", str(len(compressed)), "_blob.bin"],
                        stdout=subprocess.PIPE, text=True)
line = proc.stdout.readline().split()
pid = proc.pid
time.sleep(2)

m = get_mem(pid); io = get_io(pid)
print(f"  WHILE FALLING THROUGH BOXES:")
print(f"    process RSS: {m['VmRSS']:.1f} MB")
print(f"    RssAnon (real data cost): {m['RssAnon']:.1f} MB")
print(f"    disk write_bytes: {io['write_bytes']:,} (ROM used? {'YES' if io['write_bytes']>0 else 'NO'})")

# read a couple ticks to show it's continuously falling
for _ in range(3):
    t = proc.stdout.readline().strip()
    print(f"    {t}")

proc.wait()

print()
print("="*62)
print("  HONEST BREAKDOWN")
print("="*62)
print(f"  Full file:              {full_size:,} bytes")
print(f"  Compressed blob (held): {len(compressed):,} bytes  <- persists, to stay retrievable")
print(f"  Box (falls through):    65,536 bytes             <- only this much 'in a box' at once")
print(f"  The blob must persist to be retrievable; the box is what moves.")

os.remove("_blob.bin"); os.remove("_fallbox.c")
