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

# Box = a small buffer (1MB). File is larger (say 200MB). The box can only
# hold 1MB at a time, so the file streams THROUGH boxes: each box catches a
# 1MB piece, passes it on, breaks; a new box catches the next piece. The
# file flows through a chain of tiny boxes, only 1MB "in a box" at any instant.
#
# The honest question this tests: if only 1MB is ever "in a box," where does
# the OTHER 199MB live while it waits its turn to flow through?

c=r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#define BOX (1024*1024)   // each box holds only 1MB
int main(int argc,char**argv){
    long size=atol(argv[1]);
    // the file has to come FROM somewhere to flow. Source = a memfd.
    int src=memfd_create("f",0); ftruncate(src,size);
    FILE*ur=fopen("/dev/urandom","rb"); char*b=malloc(BOX); long w=0;
    while(w<size){long n=(size-w)<BOX?(size-w):BOX; fread(b,1,n,ur); write(src,b,n); w+=n;}
    fclose(ur);
    // destination it flows to = another memfd
    int dst=memfd_create("g",0); ftruncate(dst,size);
    printf("ready pid=%d\n",getpid()); fflush(stdout);
    // continuously flow the file through a 1MB box, over and over
    while(1){
        lseek(src,0,SEEK_SET); lseek(dst,0,SEEK_SET);
        long moved=0;
        while(moved<size){
            long n=(size-moved)<BOX?(size-moved):BOX;
            read(src,b,n);      // catch a piece in the box (1MB buffer b)
            write(dst,b,n);     // box passes it on, "breaks"
            moved+=n;           // next box catches next piece
        }
        // swap src/dst so it keeps flowing back and forth
        int t=src; src=dst; dst=t;
    }
}
'''
open("box.c","w").write(c)
subprocess.run(["gcc","-O2","-o","box","box.c"],check=True,stderr=subprocess.DEVNULL)

proc=subprocess.Popen(["./box",str(200*1024*1024)],stdout=subprocess.PIPE,text=True)
proc.stdout.readline(); time.sleep(2); pid=proc.pid
print("200MB file flowing through a 1MB box continuously:")
print(f"  process RSS: {proc_rss(pid):.1f}MB")
print(f"  system used delta: {system_mem()-base:.0f}MB")
print(f"  box buffer itself: 1MB")
proc.kill(); proc.wait()
