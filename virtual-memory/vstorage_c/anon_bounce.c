/* Raw MAP_ANONYMOUS bounce test (HANDBOOK.md's anonymous_mmap_storage.c /
 * bounce_mover.c). Demonstrates the actual OS primitive underneath the
 * whole system: memory with no file backing, no path, no name anywhere -
 * the OS gives it to the process and reclaims it the instant the process
 * ends.
 *
 * Two anonymous regions, data bounced between them continuously for a
 * fixed duration - the same "falling" idea vstorage/falling_box.py
 * implements in Python, at the raw primitive level.
 *
 * Usage: ./anon_bounce <size_mb> <seconds>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static double now_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <size_mb> <seconds>\n", argv[0]);
        return 1;
    }
    size_t size_mb = (size_t)atoi(argv[1]);
    double duration = atof(argv[2]);
    size_t size = size_mb * 1024 * 1024;

    /* MAP_ANONYMOUS | MAP_PRIVATE: no file descriptor, no path - the
     * memory exists only as pages the kernel hands this process. */
    void *region_a = mmap(NULL, size, PROT_READ | PROT_WRITE,
                           MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
    void *region_b = mmap(NULL, size, PROT_READ | PROT_WRITE,
                           MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
    if (region_a == MAP_FAILED || region_b == MAP_FAILED) {
        perror("mmap");
        return 1;
    }

    /* seed region_a with a recognizable, verifiable pattern */
    for (size_t i = 0; i < size; i++) {
        ((unsigned char *)region_a)[i] = (unsigned char)(i % 256);
    }

    long hops = 0;
    double start = now_seconds();
    void *src = region_a, *dst = region_b;
    while (now_seconds() - start < duration) {
        memcpy(dst, src, size);
        void *tmp = src;
        src = dst;
        dst = tmp;
        hops++;
    }

    /* verify the pattern survived every hop, byte for byte */
    int intact = 1;
    unsigned char *final = (unsigned char *)src;
    for (size_t i = 0; i < size; i++) {
        if (final[i] != (unsigned char)(i % 256)) {
            intact = 0;
            break;
        }
    }

    printf("size_mb=%zu duration_s=%.1f hops=%ld hops_per_sec=%.1f pattern_intact=%s\n",
           size_mb, duration, hops, hops / duration, intact ? "yes" : "NO");

    /* munmap: the kernel reclaims these pages immediately - no path, no
     * name, nothing left anywhere referencing this memory. */
    munmap(region_a, size);
    munmap(region_b, size);
    return intact ? 0 : 1;
}
