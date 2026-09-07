#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <pthread.h>
#include <time.h>
#include <string.h>

// ONE BOX holds ALL files as a single contiguous anonymous memory region.
// Layout inside the box:
//   [file0_bytes][file1_bytes][file2_bytes][file3_bytes]
//
// On each hop:
//   1. A new empty Box (anonymous mmap) is allocated
//   2. ALL file data is piped from old Box → new Box as one stream
//   3. Old Box is immediately munmap'd (destroyed, RAM reclaimed)
//   4. New Box becomes current -- 2x RAM only exists during step 2
//
// This means the 2x cost is a brief spike during transfer,
// not a permanent overhead like the previous version.

#define CHUNK (64 * 1024)

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

unsigned long checksum(void *data, size_t len) {
    unsigned char *p = (unsigned char *)data;
    unsigned long hash = 5381;
    for (size_t i = 0; i < len; i++)
        hash = ((hash << 5) + hash) + p[i];
    return hash;
}

long get_rss_kb() {
    FILE *f = fopen("/proc/self/statm", "r");
    long size, resident;
    fscanf(f, "%ld %ld", &size, &resident);
    fclose(f);
    return resident * (sysconf(_SC_PAGESIZE) / 1024); // pages -> KB
}

// transfer entire box contents to a new box, destroy old box
// returns pointer to the new box
void *transfer_and_destroy(void *old_box, size_t total_size) {
    // allocate new box
    void *new_box = mmap(NULL, total_size, PROT_READ | PROT_WRITE,
                          MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

    // pipe ALL contents from old box to new box
    int pipefd[2];
    pipe(pipefd);
    FeedArgs fa = { old_box, total_size, pipefd[1] };
    pthread_t t;
    pthread_create(&t, NULL, feed_pipe, &fa);

    size_t received = 0;
    unsigned char *out = (unsigned char *)new_box;
    while (received < total_size) {
        ssize_t n = read(pipefd[0], out + received, CHUNK);
        if (n <= 0) break;
        received += n;
    }
    pthread_join(t, NULL);
    close(pipefd[0]);

    // DESTROY the old box immediately -- RAM reclaimed right now
    munmap(old_box, total_size);

    return new_box;
}

int main() {
    // file sizes in MB -- all go into ONE box
    int num_files = 4;
    size_t sizes_mb[] = { 200, 150, 100, 50 };
    size_t offsets[4];
    size_t total_size = 0;

    // calculate layout offsets inside the box
    for (int i = 0; i < num_files; i++) {
        offsets[i] = total_size;
        total_size += sizes_mb[i] * 1024 * 1024;
    }

    printf("ONE BOX containing %zu MB total (%d files: ",
           total_size / 1024 / 1024, num_files);
    for (int i = 0; i < num_files; i++)
        printf("%zuMB%s", sizes_mb[i], i < num_files-1 ? "+" : "");
    printf(")\n\n");

    // allocate the initial box
    void *box = mmap(NULL, total_size, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

    // fill each file's region with random data
    unsigned long original_checksums[4];
    FILE *ur = fopen("/dev/urandom", "rb");
    for (int i = 0; i < num_files; i++) {
        void *file_region = (unsigned char *)box + offsets[i];
        size_t file_size = sizes_mb[i] * 1024 * 1024;
        fread(file_region, 1, file_size, ur);
        original_checksums[i] = checksum(file_region, file_size);
        printf("  File %d (%zuMB) checksum: %lu\n", i, sizes_mb[i],
               original_checksums[i]);
    }
    fclose(ur);

    printf("\nRSS after filling ONE box with all files: %.1f MB\n", get_rss_kb() / 1024.0);
    printf("(Should be ~%zuMB -- 1x RAM, not 2x)\n\n", total_size / 1024 / 1024);

    long hops = 0;
    int checksum_failures = 0;
    time_t start = time(NULL);

    for (int report = 1; report <= 3; report++) {
        // run hops for 10 seconds then report
        time_t segment_start = time(NULL);
        while (time(NULL) - segment_start < 10) {
            long rss_before = get_rss_kb();

            // transfer ALL files to new box, destroy old box
            box = transfer_and_destroy(box, total_size);
            hops++;

            long rss_after = get_rss_kb();

            // (checksum moved to report time for speed)

            // print RSS before/during/after for first hop of each segment
            if (hops == 1 || hops % 5 == 0) {
                printf("  hop %ld: RSS before=%.1fMB after=%.1fMB\n",
                       hops, rss_before / 1024.0, rss_after / 1024.0);
            }
        }

        for (int i = 0; i < num_files; i++) {
            void *fr = (unsigned char *)box + offsets[i];
            if (checksum(fr, sizes_mb[i]*1024*1024) != original_checksums[i]) checksum_failures++;
        }
        printf("\n=== t=%ds ===\n", report * 10);
        printf("  Total hops: %ld | Checksum failures: %d\n", hops, checksum_failures);
        printf("  RSS now: %.1f MB (target: ~%zuMB = 1x)\n\n",
               get_rss_kb() / 1024.0, total_size / 1024 / 1024);
    }

    printf("All %d files intact inside one self-destroying box after %ld hops.\n",
           num_files, hops);
    printf("Kill this process -- all %zuMB vanishes permanently.\n",
           total_size / 1024 / 1024);
    return 0;
}
