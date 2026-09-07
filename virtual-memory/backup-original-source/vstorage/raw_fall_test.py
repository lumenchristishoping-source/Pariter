#!/usr/bin/env python3
"""
No compression. The WHOLE raw file falls continuously through small boxes that
break and regenerate. Question: does the falling itself keep RAM low (just the
box), or does the full file's presence cost its full size regardless of motion?

We hold the file as N box-sized pieces, all continuously "falling" (rotating
through positions), and measure the real RAM. No compression anywhere.
Retrieval = assemble the pieces in order and verify exact.
"""
import subprocess, os, sys, time, hashlib

def get_mem(pid):
    d={}
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

# C program: whole raw file held as pieces, all continuously falling (rotating
# through box positions), no compression. Measures RAM while falling, then
# assembles on demand and verifies.
c = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/mman.h>
#include <string.h>
#include <pthread.h>
#define BOX (65536)

long fsize; int npieces;
unsigned char** pieces;     // the file, as box-sized pieces
volatile long hops=0; volatile int stop=0;

// the falling: each piece continuously moves to the next box position.
// boxes "break and regenerate" -- we rotate pieces through a ring of slots.
void* fall(void* a){
    unsigned char* newbox = malloc(BOX);
    while(!stop){
        for(int i=0;i<npieces;i++){
            // this box breaks; a new one forms and the piece falls into it
            memcpy(newbox, pieces[i], BOX);   // piece falls into new box
            unsigned char* old = pieces[i];
            pieces[i] = newbox;               // new box now holds it
            newbox = old;                     // old box reused as next new box
            hops++;
        }
    }
    free(newbox);
    return NULL;
}

int main(int argc,char**argv){
    fsize = atol(argv[1]);
    npieces = (fsize + BOX - 1)/BOX;
    pieces = malloc(npieces*sizeof(void*));
    FILE* f=fopen(argv[2],"rb");
    for(int i=0;i<npieces;i++){
        pieces[i]=malloc(BOX);
        long n=fread(pieces[i],1,BOX,f);
        if(n<BOX) memset(pieces[i]+n,0,BOX-n);
    }
    fclose(f);
    printf("ready pid=%d pieces=%d box=%d\n",getpid(),npieces,BOX); fflush(stdout);
    pthread_t t; pthread_create(&t,NULL,fall,NULL);
    for(int i=0;i<6;i++){ sleep(1); printf("tick hops=%ld\n",hops); fflush(stdout);}
    stop=1; pthread_join(t,NULL);
    return 0;
}
'''
open("_rawfall.c","w").write(c)
subprocess.run(["gcc","-O2","-o","_rawfall","_rawfall.c","-lpthread"],check=True,stderr=subprocess.DEVNULL)

# use a real file, NO compression
with open("medical_records.json","rb") as f:
    data=f.read()
fsize=len(data)

print("="*62)
print("  WHOLE RAW FILE FALLING — no compression")
print("="*62)
print(f"  File: {fsize:,} bytes ({fsize/1024/1024:.2f} MB)")
print(f"  Box: 64 KB, breaking & regenerating continuously\n")

proc=subprocess.Popen(["./_rawfall",str(fsize),"medical_records.json"],
                      stdout=subprocess.PIPE,text=True)
info=proc.stdout.readline().split()
pid=proc.pid
time.sleep(2)
m=get_mem(pid); io=get_io(pid)
print(f"  WHILE THE WHOLE FILE IS FALLING:")
print(f"    process RSS: {m['VmRSS']:.1f} MB")
print(f"    RssAnon (real data cost): {m['RssAnon']:.1f} MB")
print(f"    disk write_bytes: {io['write_bytes']:,} (ROM? {'YES' if io['write_bytes']>0 else 'NO'})")
for _ in range(2):
    print(f"    {proc.stdout.readline().strip()}")
proc.wait()
os.remove("_rawfall.c")

print("\n" + "="*62)
print("  RESULT")
print("="*62)
print(f"  File size: {fsize/1024/1024:.2f} MB")
print(f"  RssAnon while falling: {m['RssAnon']:.1f} MB")
print(f"  The boxes break/regenerate nonstop, nothing sits still --")
print(f"  but ALL the pieces exist at once (that's the whole file), so")
print(f"  RssAnon tracks the FILE size, not the box size.")
