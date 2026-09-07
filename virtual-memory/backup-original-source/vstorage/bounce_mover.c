#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/resource.h>
#include <time.h>
#include <string.h>

#define CHUNK (256 * 1024)

long get_peak_rss_kb() {
    struct rusage usage;
    getrusage(RUSAGE_SELF, &usage);
    return usage.ru_maxrss;
}

long get_current_time_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

// splice all bytes from src_path to dst_path via a pipe, zero-copy
long move_file(const char *src_path, const char *dst_path, long file_size) {
    int src_fd = open(src_path, O_RDONLY);
    int dst_fd = open(dst_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    int pipefd[2];
    pipe(pipefd);

    long total = 0;
    while (total < file_size) {
        ssize_t n = splice(src_fd, NULL, pipefd[1], NULL, CHUNK, SPLICE_F_MOVE);
        if (n <= 0) break;
        ssize_t remaining = n;
        while (remaining > 0) {
            ssize_t w = splice(pipefd[0], NULL, dst_fd, NULL, remaining, SPLICE_F_MOVE);
            if (w <= 0) break;
            remaining -= w;
        }
        total += n;
    }
    close(src_fd);
    close(dst_fd);
    close(pipefd[0]);
    close(pipefd[1]);
    return total;
}

int main(int argc, char *argv[]) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s <fileA> <fileB> <duration_seconds>\n", argv[0]);
        return 1;
    }
    const char *file_a = argv[1];
    const char *file_b = argv[2];
    int duration_sec = atoi(argv[3]);

    // get the starting file size from whichever side currently holds data
    FILE *f = fopen(file_a, "rb");
    fseek(f, 0, SEEK_END);
    long file_size = ftell(f);
    fclose(f);

    printf("Starting continuous bounce for %d seconds. File size: %.2f MB\n",
           duration_sec, file_size / 1024.0 / 1024.0);
    printf("Elapsed(s) | Hops so far | Peak RSS so far (MB)\n");

    long start_ms = get_current_time_ms();
    long deadline_ms = start_ms + (long)duration_sec * 1000;
    long next_report_ms = start_ms + 10000; // report every 10s

    long hops = 0;
    long peak_rss = get_peak_rss_kb();
    int current_holder_is_a = 1; // file A currently holds the data

    while (get_current_time_ms() < deadline_ms) {
        if (current_holder_is_a) {
            move_file(file_a, file_b, file_size);
        } else {
            move_file(file_b, file_a, file_size);
        }
        current_holder_is_a = !current_holder_is_a;
        hops++;

        long rss = get_peak_rss_kb();
        if (rss > peak_rss) peak_rss = rss;

        long now = get_current_time_ms();
        if (now >= next_report_ms) {
            printf("%8ld   | %11ld | %.3f\n",
                   (now - start_ms) / 1000, hops, peak_rss / 1024.0);
            next_report_ms += 10000;
        }
    }

    long final_rss = get_peak_rss_kb();
    if (final_rss > peak_rss) peak_rss = final_rss;

    printf("\nFinished. Total runtime: %d seconds\n", duration_sec);
    printf("Total hops (file moved back and forth): %ld\n", hops);
    printf("Final peak RSS across the ENTIRE run: %ld KB (%.3f MB)\n",
           peak_rss, peak_rss / 1024.0);
    printf("Data currently resting in: %s\n", current_holder_is_a ? file_a : file_b);

    return 0;
}
