
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/mman.h>
#include <string.h>
#include <pthread.h>
#define BOXSZ (1024*1024)
#define NPIECES 200
// 200 pieces of 1MB = 200MB file. Claim: only 3 boxes hold it, circulating.
// To be retrievable, each piece's bytes must be reachable. Let's see what
// that actually requires in RAM.

char* storage[NPIECES];  // where pieces actually live
unsigned long piece_checksum[NPIECES];
long file_size;
volatile int stop=0;

unsigned long csum(char*d,long n){unsigned long h=5381;for(long i=0;i<n;i++)h=((h<<5)+h)+(unsigned char)d[i];return h;}

// the "circulation": 3 boxes rotating, continuously moving pieces around
void* circulate(void*arg){
    char* box[3];
    for(int i=0;i<3;i++) box[i]=malloc(BOXSZ);
    int cur=0;
    while(!stop){
        for(int p=0;p<NPIECES;p++){
            memcpy(box[cur], storage[p], BOXSZ);   // box catches piece
            memcpy(storage[p], box[cur], BOXSZ);   // box passes it back (circulates)
            cur=(cur+1)%3;
        }
    }
    return NULL;
}

int main(int argc,char**argv){
    file_size=(long)NPIECES*BOXSZ;
    // create the real 200MB file as 200 pieces
    FILE*ur=fopen("/dev/urandom","rb");
    for(int p=0;p<NPIECES;p++){
        storage[p]=malloc(BOXSZ);
        fread(storage[p],1,BOXSZ,ur);
        piece_checksum[p]=csum(storage[p],BOXSZ);
    }
    fclose(ur);
    printf("ready pid=%d file=%dMB\n",getpid(),NPIECES); fflush(stdout);

    pthread_t t; pthread_create(&t,NULL,circulate,NULL);
    sleep(3);
    // NOW try to retrieve piece #150 while it's circulating
    int check=150;
    unsigned long now=csum(storage[check],BOXSZ);
    printf("retrieve piece #%d: %s\n", check,
           now==piece_checksum[check]?"CORRECT - file is retrievable":"CORRUPTED");
    fflush(stdout);
    sleep(30);
    stop=1; pthread_join(t,NULL);
    return 0;
}
