
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
