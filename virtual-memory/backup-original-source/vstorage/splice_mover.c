// Minimal zero-copy file mover using splice().
//
// splice() moves data directly between two file descriptors via the
// kernel's internal pipe buffer -- the bytes never get copied into this
// program's own heap or stack. The program itself only ever holds a
// few file descriptors (tiny integers) and loop counters.
//
// This tests the real floor: how little memory does a program need if
// it never touches the actual file bytes at the user-space level at all.

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/resource.h>
#include <string.h>

#define CHUNK (256 * 1024)  // how much splice moves per call, not a buffer we hold

long get_peak_rss_kb() {
    struct rusage usage;
    getrusage(RUSAGE_SELF, &usage);
    return usage.ru_maxrss;  // Linux reports this in KB already
}

int main(int argc, char *argv[]) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <source> <dest>\n", argv[0]);
        return 1;
    }

    const char *src_path = argv[1];
    const char *dst_path = argv[2];

    int src_fd = open(src_path, O_RDONLY);
    int dst_fd = open(dst_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);

    int pipefd[2];
    if (pipe(pipefd) == -1) {
        perror("pipe");
        return 1;
    }

    long total_moved = 0;
    long peak_rss_during = get_peak_rss_kb();

    while (1) {
        // move data from source file into the pipe -- kernel buffer only
        ssize_t n = splice(src_fd, NULL, pipefd[1], NULL, CHUNK, SPLICE_F_MOVE);
        if (n <= 0) break;

        // move that same data from the pipe into the destination file
        ssize_t remaining = n;
        while (remaining > 0) {
            ssize_t written = splice(pipefd[0], NULL, dst_fd, NULL, remaining, SPLICE_F_MOVE);
            if (written <= 0) break;
            remaining -= written;
        }

        total_moved += n;

        long current = get_peak_rss_kb();
        if (current > peak_rss_during) peak_rss_during = current;
    }

    close(src_fd);
    close(dst_fd);
    close(pipefd[0]);
    close(pipefd[1]);

    printf("Total bytes moved via splice (zero-copy): %ld\n", total_moved);
    printf("Peak RSS of this process during the whole transfer: %ld KB (%.3f MB)\n",
           peak_rss_during, peak_rss_during / 1024.0);

    return 0;
}
