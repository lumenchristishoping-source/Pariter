
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
