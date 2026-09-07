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

// CORRECTED design: the data lives in anonymous mmap'd memory (Linux's own
// term for memory backed by no file at all -- not disk, not tmpfs, not a
// named path, just a raw memory region the kernel gives this process and
// reclaims completely the instant the process dies).
//
// Anonymous PIPES are used only for the brief in-flight motion between two
// such regions -- a proper concurrent writer thread feeds the pipe while
// the main thread drains it, so we never try to make a pipe "hold" more
// than it's designed for.

#define CHUNK (64 * 1024) // stay under the pipe's real capacity

typedef struct {
    void *data;
    size_t size;
} WriterArgs;

void *writer_thread(void *arg) {
    WriterArgs *args = (WriterArgs *)arg;
    // this fd is passed in via a global for simplicity in this test
    return NULL;
}

long get_peak_rss_kb() {
    struct rusage usage;
    getrusage(RUSAGE_SELF, &usage);
    return usage.ru_maxrss;
}

// simple djb2-style checksum -- no external libs needed, just needs to
// reliably detect if even one byte changed across thousands of hops
unsigned long checksum(void *data, size_t len) {
    unsigned char *p = (unsigned char *)data;
    unsigned long hash = 5381;
    for (size_t i = 0; i < len; i++) {
        hash = ((hash << 5) + hash) + p[i];
    }
    return hash;
}

// move `size` bytes from src_mem to dst_mem via a real pipe, with a proper
// concurrent writer thread so the pipe is never asked to hold more than
// CHUNK bytes at a time
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

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <data_size_bytes>\n", argv[0]);
        return 1;
    }
    size_t data_size = atol(argv[1]);

    // TWO anonymous memory regions -- no file backs either of them, no path
    // exists for either of them, nothing on disk or tmpfs represents them
    void *region_a = mmap(NULL, data_size, PROT_READ | PROT_WRITE,
                           MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    void *region_b = mmap(NULL, data_size, PROT_READ | PROT_WRITE,
                           MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

    FILE *urandom = fopen("/dev/urandom", "rb");
    fread(region_a, 1, data_size, urandom);
    fclose(urandom);

    printf("PID %d alive. %.2f MB held in anonymous mmap memory (no file, no path).\n",
           getpid(), data_size / 1024.0 / 1024.0);
    printf("Peak RSS right after allocating+filling: %.3f MB\n", get_peak_rss_kb() / 1024.0);
    unsigned long original_checksum = checksum(region_a, data_size);
    printf("Original checksum: %lu\n", original_checksum);
    fflush(stdout);

    void *current = region_a;
    void *other = region_b;
    long hops = 0;
    time_t start = time(NULL);

    while (1) {
        move_anonymous(current, other, data_size);
        void *tmp = current;
        current = other;
        other = tmp;
        hops++;

        if (hops % 20 == 0) {
            unsigned long current_checksum = checksum(current, data_size);
            printf("[alive] hops=%ld elapsed=%lds RSS=%.3f MB checksum_ok=%s\n",
                   hops, time(NULL) - start, get_peak_rss_kb() / 1024.0,
                   (current_checksum == original_checksum) ? "YES" : "NO_CORRUPTION_DETECTED");
            fflush(stdout);
        }
    }
    return 0;
}
