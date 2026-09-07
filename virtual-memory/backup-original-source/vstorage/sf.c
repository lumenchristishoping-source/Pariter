
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
