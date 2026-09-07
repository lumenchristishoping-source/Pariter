#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/resource.h>
#include <time.h>
#include <string.h>

// Continuously circulate 1GB through kernel pipes using splice().
// splice() moves bytes between file descriptors via the kernel's pipe
// buffer WITHOUT copying them into this program's address space.
//
// The whole point of this test: measure the PROCESS footprint (what an
// inspector of our program sees) while 1GB is in continuous motion, and
// report our own RSS so it can be compared against system-wide memory
// measured externally at the same time.

#define CHUNK (1024 * 1024)  // 1MB per splice call

long get_rss_kb() {
    FILE *f = fopen("/proc/self/status", "r");
    char line[256];
    long rss = 0;
    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "VmRSS: %ld", &rss) == 1) break;
    }
    fclose(f);
    return rss;
}

int main(int argc, char *argv[]) {
    long data_size = (argc > 1) ? atol(argv[1]) : (1024L * 1024 * 1024);

    // Two pipes form the loop. Data circulates: pipe1 -> pipe2 -> pipe1 ...
    // The data lives in the pipes' KERNEL buffers, never in our heap.
    // But a pipe buffer maxes at 1MB, so we can't hold 1GB "resting" in a
    // pipe -- instead we keep a backing region and splice FROM it repeatedly,
    // simulating continuous flow. We measure our process footprint throughout.

    // Backing store for the data: we DO need the bytes to exist somewhere.
    // Put them in an anonymous mmap and splice through pipes continuously.
    // The test question: does OUR PROCESS RSS reflect the full 1GB, or does
    // routing through kernel pipes keep our footprint small?

    int pipefd[2];
    pipe(pipefd);
    fcntl(pipefd[1], F_SETPIPE_SZ, CHUNK * 2);

    // create a temp fd holding the data in tmpfs-like memory via memfd
    int memfd = memfd_create("vstorage", 0);
    ftruncate(memfd, data_size);

    // fill it with random data
    FILE *ur = fopen("/dev/urandom", "rb");
    char *tmpbuf = malloc(CHUNK);
    long written = 0;
    while (written < data_size) {
        long n = (data_size - written) < CHUNK ? (data_size - written) : CHUNK;
        fread(tmpbuf, 1, n, ur);
        write(memfd, tmpbuf, n);
        written += n;
    }
    fclose(ur);
    free(tmpbuf);

    printf("PID %d: circulating %.0f MB continuously via splice() through kernel pipes.\n",
           getpid(), data_size / 1024.0 / 1024.0);
    printf("Watch this process's own RSS below vs the data size.\n");
    printf("Elapsed | Loops | OUR process RSS\n");
    fflush(stdout);

    long loops = 0;
    time_t start = time(NULL);
    time_t next_report = start;

    while (1) {
        // splice the entire file from memfd through the pipe and discard-loop
        // (read end drained back). This keeps 1GB continuously flowing through
        // the kernel pipe buffer, 1MB at a time, never into our heap.
        off_t offset = 0;
        long moved = 0;
        while (moved < data_size) {
            ssize_t n = splice(memfd, &offset, pipefd[1], NULL, CHUNK, SPLICE_F_MOVE);
            if (n <= 0) break;
            // drain the read end so the pipe never fills (data flows through)
            ssize_t drained = splice(pipefd[0], NULL, memfd, &offset /*dummy*/, n, SPLICE_F_MOVE);
            (void)drained;
            moved += n;
            // reset offset drain issue: re-seek handled by loop restart
            if (offset >= data_size) break;
        }
        loops++;

        time_t now = time(NULL);
        if (now >= next_report) {
            printf("%6lds | %5ld | %ld KB (%.2f MB)\n",
                   now - start, loops, get_rss_kb(), get_rss_kb() / 1024.0);
            fflush(stdout);
            next_report = now + 2;
        }
    }
    return 0;
}
