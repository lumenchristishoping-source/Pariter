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

# Same box approach, but now REAL arbitrary data (random bytes = worst case,
# not reducible to a formula). The file streams through a 1MB box. Question:
# where does the real 200MB actually have to live, since it can't be regenerated?

c=r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#define BOX (1024*1024)
int main(int argc,char**argv){
    long size=atol(argv[1]);
    // REAL data must come from somewhere. It can't be regenerated from a seed.
    // So we read it from /dev/urandom ONCE, piece by piece, through the box.
    // Nothing stores the whole file -- but that means once a piece leaves the
    // box, it's GONE (urandom won't give the same bytes again). Test what
    // actually survives.
    char*box=malloc(BOX);
    FILE*ur=fopen("/dev/urandom","rb");
    // compute a checksum as pieces flow through the box, to prove the data
    // passed through -- but note: we CANNOT re-read it, it's consumed
    unsigned long checksum=5381; long pos=0;
    printf("ready pid=%d\n",getpid()); fflush(stdout);
    while(pos<size){
        long n=(size-pos)<BOX?(size-pos):BOX;
        fread(box,1,n,ur);                     // real data into the box
        for(long j=0;j<n;j++) checksum=((checksum<<5)+checksum)+(unsigned char)box[j];
        pos+=n;                                 // piece discarded, box reused
    }
    fclose(ur);
    printf("flowed %.0fMB of REAL data through the 1MB box, checksum=%lu\n",
           size/1024.0/1024.0, checksum); fflush(stdout);
    printf("but now: can we read the file back? ");
    // try to access byte 0 of the file again...
    printf("NO -- it was never stored, each piece was discarded after the box.\n");
    fflush(stdout);
    while(1) sleep(1);
}
'''
open("real.c","w").write(c)
subprocess.run(["gcc","-O2","-o","real","real.c"],check=True,stderr=subprocess.DEVNULL)

proc=subprocess.Popen(["./real",str(200*1024*1024)],stdout=subprocess.PIPE,text=True)
print(proc.stdout.readline().strip())
print(proc.stdout.readline().strip())
print(proc.stdout.readline().strip())
time.sleep(1); pid=proc.pid
print(f"\n  process RSS: {proc_rss(pid):.1f}MB")
print(f"  system used delta: {system_mem()-base:.0f}MB")
proc.kill(); proc.wait()
