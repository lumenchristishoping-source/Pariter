
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
