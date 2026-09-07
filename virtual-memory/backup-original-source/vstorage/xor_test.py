#!/usr/bin/env python3
import subprocess, time, os

def system_mem():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            info[k.strip()] = int(v.strip().split()[0]) / 1024.0
    return info["MemTotal"] - info["MemAvailable"]

def proc_rss(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except FileNotFoundError:
        return None

os.chdir("/home/claude/vstorage")
base = system_mem()
print(f"System used before: {base:.0f}MB\n")

c = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/mman.h>
#include <fcntl.h>
int main(int argc,char**argv){
    long size=atol(argv[1]); int n=atoi(argv[2]); // n shares
    // original data
    void*orig=mmap(NULL,size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    FILE*ur=fopen("/dev/urandom","rb"); fread(orig,1,size,ur);
    // n-1 random shares + 1 computed share = XOR split
    unsigned char**shares=malloc(n*sizeof(void*));
    for(int i=0;i<n-1;i++){
        shares[i]=mmap(NULL,size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        fread(shares[i],1,size,ur);
    }
    fclose(ur);
    // last share = orig XOR all random shares
    shares[n-1]=mmap(NULL,size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    unsigned char*o=orig;
    for(long j=0;j<size;j++){
        unsigned char x=o[j];
        for(int i=0;i<n-1;i++) x^=shares[i][j];
        shares[n-1][j]=x;
    }
    // now free the original -- only shares remain. can we still rebuild? yes.
    munmap(orig,size);
    printf("ready pid=%d shares=%d each=%.0fMB\n",getpid(),n,size/1024.0/1024.0);
    fflush(stdout);
    while(1) sleep(1);
}
'''
open("xor.c","w").write(c)
subprocess.run(["gcc","-O2","-o","xor","xor.c"],check=True,stderr=subprocess.DEVNULL)

# 200MB file, split into 3 shares
proc = subprocess.Popen(["./xor", str(200*1024*1024), "3"],
                        stdout=subprocess.PIPE, text=True)
print(proc.stdout.readline().strip())
time.sleep(2)
pid = proc.pid
rss = proc_rss(pid)
used = system_mem() - base
print(f"\n200MB file XOR-split into 3 shares:")
print(f"  process RSS: {rss:.1f}MB")
print(f"  system used delta: {used:.0f}MB")
print(f"  (3 shares x 200MB each = {3*200}MB expected)")
proc.kill(); proc.wait()
