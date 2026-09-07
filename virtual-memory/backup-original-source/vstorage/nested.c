
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/mman.h>
#include <string.h>
#include <time.h>

// A box holds the file. 3 boxes nested. When innermost breaks, the containing
// box becomes holder, and a fresh box forms as new outermost -- always 3.
typedef struct { void* data; long size; } Box;

int main(int argc,char**argv){
    long file_size=atol(argv[1]);

    // The file's actual bytes must exist. Put them in the innermost box.
    // The 3 boxes each need to be able to hold the file to pass it along.
    Box boxes[3];
    FILE*ur=fopen("/dev/urandom","rb");
    // innermost box holds the real file
    boxes[2].data=mmap(NULL,file_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    boxes[2].size=file_size;
    fread(boxes[2].data,1,file_size,ur);
    fclose(ur);
    // outer two boxes are containers -- do THEY need to be file-sized to hold it?
    boxes[1].data=mmap(NULL,file_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    boxes[1].size=file_size;
    boxes[0].data=mmap(NULL,file_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    boxes[0].size=file_size;

    unsigned long checksum=5381;
    unsigned char*p=boxes[2].data;
    for(long i=0;i<file_size;i++) checksum=((checksum<<5)+checksum)+p[i];

    printf("ready pid=%d file=%.0fMB boxes=3\n",getpid(),file_size/1024.0/1024.0);
    fflush(stdout);

    // continuous: innermost breaks -> data moves out to container -> new box forms
    int inner=2;
    while(1){
        int container=(inner+2)%3; // the box that will catch the data
        memcpy(boxes[container].data, boxes[inner].data, file_size); // catch
        // innermost "breaks" -- but we reuse its memory as the new outer box
        inner=container;
        // verify intact
        unsigned long v=5381; unsigned char*q=boxes[inner].data;
        for(long i=0;i<file_size;i++) v=((v<<5)+v)+q[i];
        if(v!=checksum){printf("LOST\n");fflush(stdout);}
    }
}
