#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <pthread.h>
#include <time.h>
#include <string.h>

#define CHUNK (64 * 1024)

typedef struct { void *src; size_t size; int write_fd; } FeedArgs;

void *feed_pipe(void *arg) {
    FeedArgs *fa = arg;
    size_t sent = 0;
    unsigned char *p = fa->src;
    while (sent < fa->size) {
        size_t n = (fa->size - sent) < CHUNK ? (fa->size - sent) : CHUNK;
        write(fa->write_fd, p + sent, n);
        sent += n;
    }
    close(fa->write_fd);
    return NULL;
}

unsigned long checksum(void *data, size_t len) {
    unsigned char *p = data;
    unsigned long h = 5381;
    for (size_t i = 0; i < len; i++) h = ((h<<5)+h)+p[i];
    return h;
}

long get_rss_kb() {
    struct rusage u; getrusage(RUSAGE_SELF, &u); return u.ru_maxrss;
}

void *transfer_and_destroy(void *old_box, size_t total_size) {
    void *new_box = mmap(NULL, total_size, PROT_READ|PROT_WRITE,
                          MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    int pipefd[2]; pipe(pipefd);
    FeedArgs fa = {old_box, total_size, pipefd[1]};
    pthread_t t; pthread_create(&t, NULL, feed_pipe, &fa);
    size_t received = 0;
    unsigned char *out = new_box;
    while (received < total_size) {
        ssize_t n = read(pipefd[0], out+received, CHUNK);
        if (n<=0) break;
        received += n;
    }
    pthread_join(t, NULL);
    close(pipefd[0]);
    munmap(old_box, total_size);  // OLD BOX DESTROYED HERE
    return new_box;
}

int main() {
    // smaller sizes for quick logic verification: 20+15+10+5 = 50MB total
    int num_files = 4;
    size_t sizes_mb[] = {20, 15, 10, 5};
    size_t offsets[4], total_size = 0;
    for (int i=0; i<num_files; i++) { offsets[i]=total_size; total_size+=sizes_mb[i]*1024*1024; }

    printf("One box, %zu MB total (%d files: 20+15+10+5MB)\n\n", total_size/1024/1024, num_files);

    void *box = mmap(NULL, total_size, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    unsigned long checksums[4];
    FILE *ur = fopen("/dev/urandom","rb");
    for (int i=0; i<num_files; i++) {
        void *r = (unsigned char*)box + offsets[i];
        fread(r, 1, sizes_mb[i]*1024*1024, ur);
        checksums[i] = checksum(r, sizes_mb[i]*1024*1024);
    }
    fclose(ur);

    printf("RSS with ONE box (all files inside): %.1f MB (target ~%zuMB = 1x)\n",
           get_rss_kb()/1024.0, total_size/1024/1024);
    fflush(stdout);

    long hops=0; int failures=0;
    time_t start = time(NULL);

    while (time(NULL)-start < 15) {
        long before = get_rss_kb();
        box = transfer_and_destroy(box, total_size);
        long after = get_rss_kb();
        hops++;

        // verify all files
        for (int i=0; i<num_files; i++) {
            unsigned long cs = checksum((unsigned char*)box+offsets[i], sizes_mb[i]*1024*1024);
            if (cs != checksums[i]) failures++;
        }

        if (hops<=3 || hops%20==0) {
            printf("hop %3ld | RSS before=%.1fMB during_peak=??? after=%.1fMB | failures=%d\n",
                   hops, before/1024.0, after/1024.0, failures);
            fflush(stdout);
        }
    }
    printf("\nDone. %ld hops in 15s. Checksum failures: %d\n", hops, failures);
    printf("RSS final: %.1f MB\n", get_rss_kb()/1024.0);
    return 0;
}
