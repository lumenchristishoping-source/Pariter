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

# 3 boxes, each ONLY 1MB. The file is 200MB -- far too big for any box.
# So a box catches 1MB, breaks (passes it on), a new box forms. The file
# is never whole in a box; it's always flowing through, 1MB at a time,
# across 3 rotating 1MB boxes. Total box RAM = 3MB.
#
# The honest measurement: with only 3x 1MB boxes, does the 200MB actually
# get held? Or does it just flow through and vanish (like the earlier test)?
# We track: box RAM (should be ~3MB) AND whether the file still exists after.

c=r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#define BOXSZ (1024*1024)   // each box is 1MB, period

int main(int argc,char**argv){
    long file_size=atol(argv[1]);
    // 3 boxes, 1MB each = 3MB total, forever
    char* boxes[3];
    for(int i=0;i<3;i++) boxes[i]=malloc(BOXSZ);

    // The file must be SOURCED from somewhere as it flows. If it's real data
    // and only 3MB of boxes exist, the source is the question. We read from
    // urandom = the data is created as it flows, never stored whole.
    FILE*ur=fopen("/dev/urandom","rb");

    printf("ready pid=%d file=%.0fMB box_ram=3MB\n",file_size/1024.0/1024.0<0?0:getpid(),file_size/1024.0/1024.0);
    fflush(stdout);

    // continuously flow the whole 200MB through the 3 rotating 1MB boxes
    long total_flowed=0;
    int cur=0;
    while(1){
        long pos=0;
        while(pos<file_size){
            long n=(file_size-pos)<BOXSZ?(file_size-pos):BOXSZ;
            fread(boxes[cur],1,n,ur);   // box catches a 1MB piece
            // box "breaks": rotate to next box, this one is reused
            cur=(cur+1)%3;
            pos+=n;
            total_flowed+=n;
        }
    }
}
'''
open("break.c","w").write(c)
subprocess.run(["gcc","-O2","-o","break","break.c"],check=True,stderr=subprocess.DEVNULL)

proc=subprocess.Popen(["./break",str(200*1024*1024)],stdout=subprocess.PIPE,text=True)
print(proc.stdout.readline().strip())
time.sleep(2); pid=proc.pid
print(f"\n200MB flowing through 3 rotating 1MB boxes:")
print(f"  process RSS: {proc_rss(pid):.1f}MB")
print(f"  system used delta: {system_mem()-base:.0f}MB")
print(f"  box RAM: 3MB (3 x 1MB)")
proc.kill(); proc.wait()
