#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/resource.h>
#include <time.h>
#include <string.h>
#include <sys/wait.h>
#include <signal.h>

// Pure anonymous-pipe version. No file paths anywhere involved in holding
// the live data -- only two anonymous pipes (created by pipe(), never
// touching the filesystem) that this process alone holds file descriptors
// to. When this process dies, the kernel destroys both pipes and every
// byte in them, permanently, with nothing left to find.

#define BUF_SIZE (256 * 1024)

long get_peak_rss_kb() {
    struct rusage usage;
    getrusage(RUSAGE_SELF, &usage);
    return usage.ru_maxrss;
}

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <data_size_bytes>\n", argv[0]);
        return 1;
    }
    long data_size = atol(argv[1]);

    // generate the "file" purely in a heap buffer, then immediately push
    // it into an anonymous pipe -- it is NEVER written to any path
    unsigned char *buf = malloc(BUF_SIZE);

    int pipe1[2], pipe2[2];
    pipe(pipe1);
    pipe(pipe2);

    // Default Linux pipe capacity is only 64KB. Our chunks can be up to
    // 256KB, and the initial seeding write happens with nobody reading yet
    // -- without this, that write blocks forever waiting for room that
    // will never appear (a real deadlock, which is what happened on the
    // first attempt). F_SETPIPE_SZ = 1031 on Linux.
    #define F_SETPIPE_SZ 1031
    fcntl(pipe1[1], F_SETPIPE_SZ, BUF_SIZE * 2);
    fcntl(pipe2[1], F_SETPIPE_SZ, BUF_SIZE * 2);

    // seed pipe1 with the data, chunk by chunk, straight from /dev/urandom
    FILE *urandom = fopen("/dev/urandom", "rb");
    long remaining = data_size;
    while (remaining > 0) {
        long n = remaining < BUF_SIZE ? remaining : BUF_SIZE;
        fread(buf, 1, n, urandom);
        write(pipe1[1], buf, n);
        remaining -= n;
    }
    fclose(urandom);

    printf("PID %d is now alive, holding %.2f MB purely in two anonymous pipes.\n",
           getpid(), data_size / 1024.0 / 1024.0);
    printf("No filename, no path, nothing on any filesystem represents this data.\n");
    printf("Peak RSS: %.3f MB\n", get_peak_rss_kb() / 1024.0);
    fflush(stdout);

    // bounce the data between pipe1 and pipe2 forever, anonymously
    int src = pipe1[0], src_w_other_end = pipe2[1];
    int dst = pipe2[0], dst_w_other_end = pipe1[1];
    long hops = 0;
    time_t start = time(NULL);

    while (1) {
        long moved = 0;
        while (moved < data_size) {
            long n = data_size - moved < BUF_SIZE ? data_size - moved : BUF_SIZE;
            ssize_t r = read(src, buf, n);
            if (r <= 0) break;
            write(src_w_other_end, buf, r);
            moved += r;
        }
        hops++;
        // swap direction
        int t1 = src, t2 = src_w_other_end;
        src = dst; src_w_other_end = dst_w_other_end;
        dst = t1; dst_w_other_end = t2;

        if (hops % 500 == 0) {
            printf("[still alive] hops=%ld, elapsed=%lds, RSS=%.3f MB\n",
                   hops, time(NULL) - start, get_peak_rss_kb() / 1024.0);
            fflush(stdout);
        }
    }
    return 0;
}
