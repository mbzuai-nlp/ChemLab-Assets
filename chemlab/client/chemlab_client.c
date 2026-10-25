/* chemlab thin client: sends argv to the chemlab core service over a local UNIX socket and prints the reply.
 *
 * All parsing, checks and rules live in the core service; this program only carries the request.  It is the one
 * program in the agent sandbox that can open a UNIX socket.
 *
 * Environment: CHEMLAB_SOCKET  path of the service socket (its file name must be lab.sock)
 *              CHEMLAB_TOKEN   per-run token the service checks
 * Wire: request  = one JSON line {"token": "...", "argv": ["chemlab", ...]}
 *       reply    = "CHEMLAB/1 <exit> <stdout bytes> <stderr bytes>\n" + stdout + stderr
 * Exit: the service's exit code; 5 when the service cannot be reached.
 * Build: cc -O2 -static -o chemlab chemlab_client.c   (falls back to dynamic linking)
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

static char *buf;
static size_t len, cap;

static void put(const char *s, size_t n) {
    if (len + n + 1 > cap) {
        cap = (len + n + 1) * 2;
        buf = realloc(buf, cap);
        if (!buf) { fputs("chemlab: out of memory\n", stderr); exit(5); }
    }
    memcpy(buf + len, s, n);
    len += n;
}

static void put_json_string(const char *s) {
    put("\"", 1);
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        char esc[8];
        if (*p == '"' || *p == '\\') { esc[0] = '\\'; esc[1] = (char)*p; put(esc, 2); }
        else if (*p < 0x20) { snprintf(esc, sizeof esc, "\\u%04x", *p); put(esc, 6); }
        else put((const char *)p, 1);
    }
    put("\"", 1);
}

static int fail(const char *what) {
    fprintf(stderr, "error[service/UNREACHABLE]: %s (%s).\nfix: the chemlab service is not running for this shell; "
                    "ask the operator. Nothing was sent to the instrument.\ninstrument changed: no\n", what, strerror(errno));
    return 5;
}

static int read_full(int fd, char *dst, size_t n) {
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, dst + got, n - got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return -1;
        got += (size_t)r;
    }
    return 0;
}

int main(int argc, char **argv) {
    const char *path = getenv("CHEMLAB_SOCKET"), *token = getenv("CHEMLAB_TOKEN");
    if (!path || !*path) { errno = ENOENT; return fail("CHEMLAB_SOCKET is not set"); }
    const char *slash = strrchr(path, '/');
    const char *name = slash ? slash + 1 : path;
    if (strcmp(name, "lab.sock") != 0) { errno = EINVAL; return fail("CHEMLAB_SOCKET must name a lab.sock file"); }

    /* Connect with a relative name from inside the socket's directory: socket paths are limited to 108 bytes. */
    int here = open(".", O_PATH | O_CLOEXEC);
    if (slash) {
        char *dir = strndup(path, (size_t)(slash - path));
        int dfd = open(dir[0] ? dir : "/", O_PATH | O_DIRECTORY | O_CLOEXEC);
        free(dir);
        if (dfd < 0 || fchdir(dfd) != 0) return fail("cannot open the socket directory");
        close(dfd);
    }
    int s = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (s < 0) return fail("cannot create a socket");
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    strcpy(addr.sun_path, "lab.sock");
    int rc = connect(s, (struct sockaddr *)&addr, sizeof addr);
    if (here >= 0) { if (fchdir(here) != 0) { /* keep going: cwd only matters for nothing below */ } close(here); }
    if (rc != 0) return fail("cannot connect to the chemlab service");

    put("{\"token\":", 9);
    put_json_string(token ? token : "");
    put(",\"argv\":[", 9);
    for (int i = 0; i < argc; i++) {
        const char *a = argv[i];
        if (i == 0) { const char *b = strrchr(a, '/'); a = b ? b + 1 : a; }
        if (i) put(",", 1);
        put_json_string(a);
    }
    put("]}\n", 3);
    for (size_t off = 0; off < len;) {
        ssize_t w = write(s, buf + off, len - off);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return fail("cannot send the request");
        off += (size_t)w;
    }
    shutdown(s, SHUT_WR);

    char head[128];
    size_t h = 0;
    while (h + 1 < sizeof head) {
        ssize_t r = read(s, head + h, 1);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return fail("the service closed the connection");
        if (head[h++] == '\n') break;
    }
    head[h] = 0;
    int code;
    size_t nout, nerr;
    if (sscanf(head, "CHEMLAB/1 %d %zu %zu", &code, &nout, &nerr) != 3) { errno = EPROTO; return fail("bad reply header"); }
    char *out = malloc(nout + nerr + 1);
    if (!out || read_full(s, out, nout + nerr) != 0) return fail("short reply");
    fwrite(out, 1, nout, stdout);
    fflush(stdout);
    fwrite(out + nout, 1, nerr, stderr);
    return code;
}
