
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
