#!/usr/bin/env python3
"""
The honest full-picture test of the splice() result.

Question: when 1GB flows continuously through kernel pipes via splice(),
- what does OUR PROCESS footprint look like (what an inspector sees)?
- where does the full 1GB actually live (system-wide accounting)?

We measure BOTH, from outside, at the same time.
"""
import subprocess, time, os

def system_mem():
    """Return (used_MB, free_MB, available_MB) system-wide from /proc/meminfo."""
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            info[k.strip()] = int(v.strip().split()[0]) / 1024.0  # KB->MB
    total = info["MemTotal"]
    free = info["MemFree"]
    avail = info["MemAvailable"]
    return total - avail, free, avail

def proc_rss(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except FileNotFoundError:
        return None
    return None

os.chdir("/home/claude/vstorage")

# Baseline system memory before we do anything
base_used, base_free, base_avail = system_mem()
print(f"System memory BEFORE: used={base_used:.0f}MB free={base_free:.0f}MB\n")

# Build a simple continuous splice flow program
c_code = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#define CHUNK (1024*1024)
int main(int argc, char**argv){
    long size = atol(argv[1]);
    // hold the data in a memfd (kernel-backed, anonymous, no path)
    int memfd = memfd_create("vs", 0);
    ftruncate(memfd, size);
    FILE*ur=fopen("/dev/urandom","rb");
    char*b=malloc(CHUNK); long w=0;
    while(w<size){long n=(size-w)<CHUNK?(size-w):CHUNK; fread(b,1,n,ur); write(memfd,b,n); w+=n;}
    fclose(ur); free(b);
    // continuously splice the whole file into a pipe and drain it, forever
    int p[2]; pipe(p); fcntl(p[1],F_SETPIPE_SZ,CHUNK);
    printf("ready pid=%d\n", getpid()); fflush(stdout);
    while(1){
        off_t off=0; long moved=0;
        while(moved<size){
            ssize_t n=splice(memfd,&off,p[1],NULL,CHUNK,SPLICE_F_MOVE);
            if(n<=0)break;
            char tmp[CHUNK]; ssize_t r=read(p[0],tmp,n); (void)r;
            moved+=n;
        }
    }
}
'''
with open("sf.c","w") as f:
    f.write(c_code)
subprocess.run(["gcc","-O2","-o","sf","sf.c"], check=True,
               stderr=subprocess.DEVNULL)

# Launch it holding 1GB
proc = subprocess.Popen(["./sf", str(1024*1024*1024)],
                        stdout=subprocess.PIPE, text=True)
# wait for "ready"
proc.stdout.readline()
pid = proc.pid
time.sleep(2)

print(f"1GB now circulating continuously via splice(). PID={pid}\n")
print(f"{'time':>5} | {'OUR process RSS':>16} | {'system used (delta)':>20}")
print("-"*50)
for i in range(6):
    rss = proc_rss(pid)
    used, free, avail = system_mem()
    delta = used - base_used
    print(f"{i*2:>4}s | {rss:>13.1f}MB | {delta:>15.0f}MB more")
    time.sleep(2)

proc.kill()
proc.wait()

# system memory after killing it
time.sleep(1)
after_used, after_free, after_avail = system_mem()
print("-"*50)
print(f"\nAfter kill: system used returned to {after_used - base_used:+.0f}MB vs baseline")
print("\nINTERPRETATION:")
print("  'OUR process RSS' = what an inspector of the program sees.")
print("  'system used delta' = where the actual 1GB physically lives.")
