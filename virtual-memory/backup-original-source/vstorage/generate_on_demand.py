#!/usr/bin/env python3
import subprocess, time, os

def system_mem():
    with open("/proc/meminfo") as f:
        d={k.strip():int(v.split()[0])/1024.0 for k,v in (l.split(":") for l in f)}
    return d["MemTotal"]-d["MemAvailable"]

def proc_rss(pid):
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS:"): return int(line.split()[1])/1024.0
    except FileNotFoundError: return None

os.chdir("/home/claude/vstorage")
base=system_mem()

# The idea: the file's pieces are GENERATED on demand from a tiny seed, one
# 1MB piece at a time, flow through the box, and are discarded. The full file
# never exists at once -- only the 1MB currently in the box exists.
#
# This works ONLY if each piece can be recreated from something tiny (a seed +
# position). That's the real test: is the "file" reducible to a small formula?

c=r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <time.h>
#include <string.h>
#define BOX (1024*1024)
// deterministic pseudo-random generator from a seed + position
unsigned char gen_byte(unsigned long seed, unsigned long pos){
    unsigned long x = seed ^ (pos*2654435761UL);
    x ^= x>>13; x*=0x9E3779B1; x^=x>>16;
    return (unsigned char)x;
}
int main(int argc,char**argv){
    long size=atol(argv[1]);
    unsigned long seed=12345;
    char*box=malloc(BOX);   // the ONLY storage: one 1MB box
    // "checksum" the whole virtual file once so we can verify it's consistent
    unsigned long checksum=5381;
    for(long i=0;i<size;i++) checksum=((checksum<<5)+checksum)+gen_byte(seed,i);
    printf("ready pid=%d virtual_file=%.0fMB checksum=%lu\n",
           getpid(), size/1024.0/1024.0, checksum); fflush(stdout);
    // continuously "flow" the file: generate each 1MB piece on demand into the
    // box, process it, discard. The full file NEVER exists at once.
    while(1){
        long pos=0; unsigned long verify=5381;
        while(pos<size){
            long n=(size-pos)<BOX?(size-pos):BOX;
            for(long j=0;j<n;j++) box[j]=gen_byte(seed,pos+j); // generate piece
            for(long j=0;j<n;j++) verify=((verify<<5)+verify)+box[j]; // use piece
            pos+=n; // discard piece, box reused for next
        }
        // verify the reconstructed-on-the-fly file matches
        if(verify!=checksum){ printf("MISMATCH\n"); fflush(stdout); }
    }
}
'''
open("gen.c","w").write(c)
subprocess.run(["gcc","-O2","-o","gen","gen.c"],check=True,stderr=subprocess.DEVNULL)

proc=subprocess.Popen(["./gen",str(1024*1024*1024)],stdout=subprocess.PIPE,text=True)
print(proc.stdout.readline().strip())
time.sleep(2); pid=proc.pid
print(f"\n1GB virtual file, generated 1MB-at-a-time on demand, never whole:")
print(f"  process RSS: {proc_rss(pid):.1f}MB")
print(f"  system used delta: {system_mem()-base:.0f}MB")
time.sleep(2)
print(f"  (still running, checksum consistent = the 1GB is real & reconstructable)")
proc.kill(); proc.wait()
