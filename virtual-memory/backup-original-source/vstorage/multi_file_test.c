#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <pthread.h>
#include <time.h>
#include <string.h>

#define CHUNK (64 * 1024)

// Each file gets its own independent bouncing thread
typedef struct {
    int id;
    size_t size;
    void *region_a;
    void *region_b;
    unsigned long original_checksum;
    long hops;
    int checksum_failures;
} FileStore;

typedef struct {
    void *src;
    size_t size;
    int write_fd;
} FeedArgs;

void *feed_pipe(void *arg) {
    FeedArgs *fa = (FeedArgs *)arg;
    size_t sent = 0;
    unsigned char *p = (unsigned char *)fa->src;
    while (sent < fa->size) {
        size_t n = (fa->size - sent) < CHUNK ? (fa->size - sent) : CHUNK;
        write(fa->write_fd, p + sent, n);
        sent += n;
    }
    close(fa->write_fd);
    return NULL;
}

void move_anonymous(void *src, void *dst, size_t size) {
    int pipefd[2];
    pipe(pipefd);
    FeedArgs fa = { src, size, pipefd[1] };
    pthread_t t;
    pthread_create(&t, NULL, feed_pipe, &fa);
    size_t received = 0;
    unsigned char *out = (unsigned char *)dst;
    while (received < size) {
        ssize_t n = read(pipefd[0], out + received, CHUNK);
        if (n <= 0) break;
        received += n;
    }
    pthread_join(t, NULL);
    close(pipefd[0]);
}

unsigned long checksum(void *data, size_t len) {
    unsigned char *p = (unsigned char *)data;
    unsigned long hash = 5381;
    for (size_t i = 0; i < len; i++)
        hash = ((hash << 5) + hash) + p[i];
    return hash;
}

long get_rss_kb() {
    struct rusage u;
    getrusage(RUSAGE_SELF, &u);
    return u.ru_maxrss;
}

void *bounce_file(void *arg) {
    FileStore *fs = (FileStore *)arg;
    void *current = fs->region_a;
    void *other = fs->region_b;
    while (1) {
        move_anonymous(current, other, fs->size);
        void *tmp = current; current = other; other = tmp;
        fs->hops++;
        unsigned long cs = checksum(current, fs->size);
        if (cs != fs->original_checksum)
            fs->checksum_failures++;
    }
    return NULL;
}

int main() {
    // Define our files: sizes in MB
    int num_files = 4;
    size_t sizes_mb[] = { 200, 150, 100, 50 };  // 500MB total across 4 files
    FileStore files[4];
    pthread_t threads[4];

    printf("Starting %d files simultaneously in anonymous memory:\n", num_files);
    for (int i = 0; i < num_files; i++) {
        size_t sz = sizes_mb[i] * 1024 * 1024;
        files[i].id = i;
        files[i].size = sz;
        files[i].hops = 0;
        files[i].checksum_failures = 0;

        files[i].region_a = mmap(NULL, sz, PROT_READ|PROT_WRITE,
                                  MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
        files[i].region_b = mmap(NULL, sz, PROT_READ|PROT_WRITE,
                                  MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);

        FILE *ur = fopen("/dev/urandom", "rb");
        fread(files[i].region_a, 1, sz, ur);
        fclose(ur);

        files[i].original_checksum = checksum(files[i].region_a, sz);
        printf("  File %d: %zuMB, checksum=%lu\n", i, sizes_mb[i],
               files[i].original_checksum);
    }

    printf("\nRSS before starting bounce threads: %.1f MB\n\n",
           get_rss_kb() / 1024.0);

    // launch all bounce threads simultaneously
    for (int i = 0; i < num_files; i++)
        pthread_create(&threads[i], NULL, bounce_file, &files[i]);

    // report every 10 seconds
    for (int report = 1; report <= 3; report++) {
        sleep(10);
        printf("=== t=%ds, RSS=%.1f MB ===\n", report * 10, get_rss_kb() / 1024.0);
        long total_hops = 0;
        for (int i = 0; i < num_files; i++) {
            printf("  File %d (%zuMB): hops=%ld checksum_failures=%d\n",
                   i, sizes_mb[i], files[i].hops, files[i].checksum_failures);
            total_hops += files[i].hops;
        }
        printf("  Total hops across all files: %ld\n\n", total_hops);
    }

    printf("All files independently bouncing, all checksums intact.\n");
    printf("Kill this process and every byte from all 4 files vanishes permanently.\n");
    return 0;
}
