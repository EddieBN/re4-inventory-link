#include "net.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

static int make_addr(const char *ip, int port, struct sockaddr_in *sa)
{
    memset(sa, 0, sizeof(*sa));
    sa->sin_family = AF_INET;
    sa->sin_port = htons(port);
    return inet_aton(ip, &sa->sin_addr) ? 0 : -1;
}

static int connect_timeout(int fd, struct sockaddr_in *sa, int timeout_ms)
{
    int fl = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, fl | O_NONBLOCK);
    int r = connect(fd, (struct sockaddr *)sa, sizeof(*sa));
    if (r < 0 && errno != EINPROGRESS)
        return -1;
    if (r < 0) {
        struct pollfd p = {.fd = fd, .events = POLLOUT};
        if (poll(&p, 1, timeout_ms) <= 0)
            return -1;
        int err = 0;
        socklen_t len = sizeof(err);
        getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len);
        if (err)
            return -1;
    }
    fcntl(fd, F_SETFL, fl);
    return 0;
}

int http_request(const char *ip, int port, const char *method, const char *path,
                 const char *body, char **out, int *out_len, int timeout_ms)
{
    *out = NULL;
    if (out_len)
        *out_len = 0;
    struct sockaddr_in sa;
    if (make_addr(ip, port, &sa) < 0)
        return -1;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return -1;
    if (connect_timeout(fd, &sa, 2000) < 0) {
        close(fd);
        return -1;
    }
    struct timeval tv = {.tv_sec = timeout_ms / 1000, .tv_usec = (timeout_ms % 1000) * 1000};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    int blen = body ? (int)strlen(body) : 0;
    char hdr[768];
    int hl = snprintf(hdr, sizeof(hdr),
                      "%s %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\n"
                      "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n",
                      method, path, ip, port, blen);
    if (send(fd, hdr, hl, 0) != hl || (blen && send(fd, body, blen, 0) != blen)) {
        close(fd);
        return -1;
    }
    int cap = 16384, len = 0;
    char *buf = malloc(cap);
    for (;;) {
        if (len + 4096 > cap) {
            cap *= 2;
            if (cap > 8 * 1024 * 1024)
                break;
            buf = realloc(buf, cap);
        }
        int n = recv(fd, buf + len, cap - len - 1, 0);
        if (n <= 0)
            break;
        len += n;
    }
    close(fd);
    buf[len] = 0;
    int status = -1;
    if (len > 12 && sscanf(buf, "HTTP/1.%*d %d", &status) != 1)
        status = -1;
    char *sep = strstr(buf, "\r\n\r\n");
    if (!sep || status < 0) {
        free(buf);
        return -1;
    }
    sep += 4;
    int bl = len - (int)(sep - buf);
    char *res = malloc(bl + 1);
    memcpy(res, sep, bl);
    res[bl] = 0;
    free(buf);
    *out = res;
    if (out_len)
        *out_len = bl;
    return status;
}

// ------------------------------------------------------------------ UDP
static int g_pad = -1;
static struct sockaddr_in g_pad_addr;

int pad_open(const char *ip, int port)
{
    pad_close();
    if (make_addr(ip, port, &g_pad_addr) < 0)
        return -1;
    g_pad = socket(AF_INET, SOCK_DGRAM, 0);
    if (g_pad < 0)
        return -1;
    fcntl(g_pad, F_SETFL, fcntl(g_pad, F_GETFL, 0) | O_NONBLOCK);
    return 0;
}

void pad_close(void)
{
    if (g_pad >= 0)
        close(g_pad);
    g_pad = -1;
}

void pad_send(uint32_t seq, uint16_t buttons, uint8_t lt, uint8_t rt,
              int16_t lx, int16_t ly, int16_t rx, int16_t ry)
{
    if (g_pad < 0)
        return;
    uint8_t p[20];
    memcpy(p, "RE4P", 4);
    memcpy(p + 4, &seq, 4);
    memcpy(p + 8, &buttons, 2);
    p[10] = lt;
    p[11] = rt;
    memcpy(p + 12, &lx, 2);
    memcpy(p + 14, &ly, 2);
    memcpy(p + 16, &rx, 2);
    memcpy(p + 18, &ry, 2);
    sendto(g_pad, p, sizeof(p), 0, (struct sockaddr *)&g_pad_addr, sizeof(g_pad_addr));
}

int pad_poll_rumble(uint16_t *left, uint16_t *right)
{
    if (g_pad < 0)
        return 0;
    uint8_t p[16];
    int got = 0;
    for (;;) {
        int n = recv(g_pad, p, sizeof(p), 0);
        if (n <= 0)
            break;
        if (n == 8 && memcmp(p, "RE4R", 4) == 0) {
            memcpy(left, p + 4, 2);
            memcpy(right, p + 6, 2);
            got = 1;
        }
    }
    return got;
}
