// RE4 Inventory — homebrew do Switch
// Mostra a maleta do Resident Evil 4 que roda no PC (servidor RE4 Inventory Link) e usa os
// controles do Switch como controle do jogo (enviados por UDP; o PC os injeta como XInput).
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <switch.h>
#include <sys/stat.h>
#include <unistd.h>

#include <SDL.h>
#include <SDL_image.h>
#include <SDL_ttf.h>

#include "net.h"

#define SCR_W 1280
#define SCR_H 720
#define BAR_H 52
#define HTTP_PORT 8044
#define PAD_PORT 8045
#define CFG_DIR "sdmc:/switch/re4inv"
#define CFG_FILE CFG_DIR "/config.txt"
#define MAX_ITEMS 64
#define MAX_OTHERS 128

// botões XInput
enum { XI_UP = 0x1, XI_DOWN = 0x2, XI_LEFT = 0x4, XI_RIGHT = 0x8, XI_START = 0x10, XI_BACK = 0x20,
       XI_LS = 0x40, XI_RS = 0x80, XI_LB = 0x100, XI_RB = 0x200, XI_A = 0x1000, XI_B = 0x2000,
       XI_X = 0x4000, XI_Y = 0x8000 };

// ===================================================================== estado
typedef struct {
    int slot, id, x, y, w, h, rot, eq, count, type, ammo, ammoMax, fp, fs, rs, cap, num, max;
    char icon[192], name[96];
} Item;
typedef struct {
    int slot, type, num;
    char name[96];
} Other;
typedef struct {
    int valid, connected, running, W, H, level, version;
    char error[192];
    Item items[MAX_ITEMS];
    int nitems;
    Other others[MAX_OTHERS];
    int nothers;
} State;

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static State g_state;               // protegido por g_lock
static int g_net_ok = 0;            // última requisição deu certo
static char g_ip[64] = "";
static int g_ip_gen = 0;            // muda quando o IP muda
static int g_layout = 0;            // 0 = posição (estilo Xbox), 1 = rótulos Nintendo
static volatile int g_quit = 0;
static volatile int g_pad_reopen = 0;   // o IP mudou: a thread principal reabre o socket UDP
static volatile int g_discover_now = 0; // pedido de busca do PC na rede
static volatile int g_discovering = 0;

static char g_toast[256];
static u64 g_toast_until = 0;

static u64 now_ms(void) { return armTicksToNs(armGetSystemTick()) / 1000000ULL; }

static void toast(const char *msg)
{
    pthread_mutex_lock(&g_lock);
    snprintf(g_toast, sizeof(g_toast), "%s", msg);
    g_toast_until = now_ms() + 2600;
    pthread_mutex_unlock(&g_lock);
}

// ===================================================================== config
static void cfg_load(void)
{
    FILE *f = fopen(CFG_FILE, "r");
    if (!f)
        return;
    char line[128];
    while (fgets(line, sizeof(line), f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!strncmp(line, "ip=", 3))
            snprintf(g_ip, sizeof(g_ip), "%s", line + 3);
        else if (!strncmp(line, "layout=", 7))
            g_layout = atoi(line + 7) ? 1 : 0;
    }
    fclose(f);
}

static void cfg_save(void)
{
    mkdir("sdmc:/switch", 0777);
    mkdir(CFG_DIR, 0777);
    FILE *f = fopen(CFG_FILE, "w");
    if (!f)
        return;
    fprintf(f, "ip=%s\nlayout=%d\n", g_ip, g_layout);
    fclose(f);
}

static void ip_copy(char *dst, int n)
{
    pthread_mutex_lock(&g_lock);
    snprintf(dst, n, "%s", g_ip);
    pthread_mutex_unlock(&g_lock);
}

// ===================================================================== parse do estado (texto)
static int split_tabs(char *line, char **f, int max)
{
    int n = 0;
    f[n++] = line;
    for (char *p = line; *p && n < max; p++)
        if (*p == '\t') {
            *p = 0;
            f[n++] = p + 1;
        }
    return n;
}

static void parse_state(char *txt, State *st)
{
    memset(st, 0, sizeof(*st));
    st->valid = 1;
    char *save = NULL;
    for (char *line = strtok_r(txt, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char *f[24];
        int n = split_tabs(line, f, 24);
        if (f[0][0] == 'V' && n >= 2)
            st->version = atoi(f[1]);
        else if (f[0][0] == 'E' && n >= 2) {
            st->connected = 0;
            snprintf(st->error, sizeof(st->error), "%s", f[1]);
        } else if (f[0][0] == 'S' && n >= 6) {
            st->connected = atoi(f[1]);
            st->running = atoi(f[2]);
            st->W = atoi(f[3]);
            st->H = atoi(f[4]);
            st->level = atoi(f[5]);
        } else if (f[0][0] == 'I' && n >= 21 && st->nitems < MAX_ITEMS) {
            Item *it = &st->items[st->nitems++];
            int *v[] = {&it->slot, &it->id, &it->x, &it->y, &it->w, &it->h, &it->rot, &it->eq, &it->count,
                        &it->type, &it->ammo, &it->ammoMax, &it->fp, &it->fs, &it->rs, &it->cap, &it->num, &it->max};
            for (int i = 0; i < 18; i++)
                *v[i] = (i == 8 && f[1 + i][0] == '-') ? -1 : atoi(f[1 + i]);
            snprintf(it->icon, sizeof(it->icon), "%s", f[19][0] == '-' ? "" : f[19]);
            snprintf(it->name, sizeof(it->name), "%s", f[20]);
        } else if (f[0][0] == 'O' && n >= 5 && st->nothers < MAX_OTHERS) {
            Other *o = &st->others[st->nothers++];
            o->slot = atoi(f[1]);
            o->type = atoi(f[2]);
            o->num = atoi(f[3]);
            snprintf(o->name, sizeof(o->name), "%s", f[4]);
        }
    }
}

// ===================================================================== thread: estado (long-poll)
// procura o PC na rede (broadcast); se achar, troca o IP e salva
static int try_discover(void)
{
    char found[64];
    g_discovering = 1;
    int ok = discover_server(PAD_PORT, found, sizeof(found), 1500) == 0;
    g_discovering = 0;
    if (!ok)
        return 0;
    int changed = 0;
    pthread_mutex_lock(&g_lock);
    if (strcmp(found, g_ip)) {
        snprintf(g_ip, sizeof(g_ip), "%s", found);
        g_ip_gen++;
        changed = 1;
    }
    pthread_mutex_unlock(&g_lock);
    if (changed) {
        cfg_save();
        g_pad_reopen = 1;
        char m[96];
        snprintf(m, sizeof(m), "PC encontrado: %s", found);
        toast(m);
    }
    return 1;
}

static void *state_thread(void *arg)
{
    (void)arg;
    int ver = -1, gen = -1, fails = 0;
    u64 last_disc = 0;
    char ip[64];
    while (!g_quit) {
        ip_copy(ip, sizeof(ip));
        // sem IP, IP que não responde (o roteador pode ter trocado o IP do PC) ou pedido manual: procura
        if (g_discover_now || ((!ip[0] || fails >= 2) && now_ms() - last_disc > 4000)) {
            g_discover_now = 0;
            last_disc = now_ms();
            if (try_discover()) {
                fails = 0;
                continue;
            }
        }
        if (!ip[0]) {
            svcSleepThread(300000000ULL);
            continue;
        }
        if (gen != g_ip_gen) {
            gen = g_ip_gen;
            ver = -1;
        }
        char path[64];
        snprintf(path, sizeof(path), "/api/switch/state?v=%d", ver);
        char *body = NULL;
        int st = http_request(ip, HTTP_PORT, "GET", path, NULL, &body, NULL, 12000);
        if (st == 200 && body && gen == g_ip_gen) {
            State *ns = malloc(sizeof(State));
            parse_state(body, ns);
            ver = ns->version;
            fails = 0;
            pthread_mutex_lock(&g_lock);
            g_state = *ns;
            g_net_ok = 1;
            pthread_mutex_unlock(&g_lock);
            free(ns);
        } else {
            fails++;
            pthread_mutex_lock(&g_lock);
            g_net_ok = 0;
            pthread_mutex_unlock(&g_lock);
            svcSleepThread(800000000ULL);
        }
        free(body);
    }
    return NULL;
}

// ===================================================================== thread: ações
typedef struct {
    char path[48], body[128], label[96];
} Action;
#define QMAX 16
static Action g_q[QMAX];
static int g_qh = 0, g_qt = 0;
static pthread_cond_t g_qcond = PTHREAD_COND_INITIALIZER;

static void post_action(const char *path, const char *body, const char *label)
{
    pthread_mutex_lock(&g_lock);
    if ((g_qt + 1) % QMAX != g_qh) {
        Action *a = &g_q[g_qt];
        snprintf(a->path, sizeof(a->path), "%s", path);
        snprintf(a->body, sizeof(a->body), "%s", body);
        snprintf(a->label, sizeof(a->label), "%s", label ? label : "");
        g_qt = (g_qt + 1) % QMAX;
        pthread_cond_signal(&g_qcond);
    }
    pthread_mutex_unlock(&g_lock);
}

// extrai "error": "..." de uma resposta JSON simples
static void json_error(const char *js, char *out, int n)
{
    const char *p = strstr(js ? js : "", "\"error\"");
    snprintf(out, n, "Erro");
    if (!p)
        return;
    p = strchr(p + 7, '"');
    if (!p)
        return;
    p++;
    int i = 0;
    while (*p && *p != '"' && i < n - 1) {
        if (*p == '\\' && p[1])
            p++;
        out[i++] = *p++;
    }
    out[i] = 0;
}

static void *action_thread(void *arg)
{
    (void)arg;
    while (!g_quit) {
        pthread_mutex_lock(&g_lock);
        while (g_qh == g_qt && !g_quit)
            pthread_cond_wait(&g_qcond, &g_lock);
        if (g_quit) {
            pthread_mutex_unlock(&g_lock);
            break;
        }
        Action a = g_q[g_qh];
        g_qh = (g_qh + 1) % QMAX;
        pthread_mutex_unlock(&g_lock);
        char ip[64];
        ip_copy(ip, sizeof(ip));
        char *res = NULL;
        int st = http_request(ip, HTTP_PORT, "POST", a.path, a.body, &res, NULL, 5000);
        if (st == 200 && res && (strstr(res, "\"ok\": true") || strstr(res, "\"ok\":true"))) {
            if (strstr(res, "\"pending\": true"))
                toast("Troca agendada — volte ao jogo no PC");
            else if (a.label[0])
                toast(a.label);
        } else if (st < 0) {
            toast("Sem conexão com o PC");
        } else {
            char err[200];
            json_error(res, err, sizeof(err));
            toast(err);
        }
        free(res);
    }
    return NULL;
}

// ===================================================================== ícones (thread baixa, main cria textura)
typedef struct {
    char url[192];
    int state;            // 0 vazio, 1 pedido, 2 bytes prontos, 3 textura, 4 falhou
    char *data;
    int len;
    SDL_Texture *tex;
    int tw, th;
} Icon;
#define ICON_MAX 128
static Icon g_icons[ICON_MAX];

static Icon *icon_get(const char *url)
{
    if (!url[0])
        return NULL;
    pthread_mutex_lock(&g_lock);
    Icon *free_slot = NULL, *hit = NULL;
    for (int i = 0; i < ICON_MAX; i++) {
        if (g_icons[i].state && !strcmp(g_icons[i].url, url)) {
            hit = &g_icons[i];
            break;
        }
        if (!g_icons[i].state && !free_slot)
            free_slot = &g_icons[i];
    }
    if (!hit && free_slot) {
        snprintf(free_slot->url, sizeof(free_slot->url), "%s", url);
        free_slot->state = 1;
        hit = free_slot;
    }
    pthread_mutex_unlock(&g_lock);
    return hit;
}

static void url_encode_path(const char *in, char *out, int n)
{
    int j = 0;
    for (const unsigned char *p = (const unsigned char *)in; *p && j < n - 4; p++) {
        if (*p == ' ' || *p >= 0x80 || *p == '#' || *p == '%') {
            j += snprintf(out + j, n - j, "%%%02X", *p);
        } else
            out[j++] = *p;
    }
    out[j] = 0;
}

static void *icon_thread(void *arg)
{
    (void)arg;
    while (!g_quit) {
        Icon *job = NULL;
        pthread_mutex_lock(&g_lock);
        for (int i = 0; i < ICON_MAX; i++)
            if (g_icons[i].state == 1) {
                job = &g_icons[i];
                break;
            }
        pthread_mutex_unlock(&g_lock);
        if (!job) {
            svcSleepThread(100000000ULL);
            continue;
        }
        char ip[64], path[400];
        ip_copy(ip, sizeof(ip));
        url_encode_path(job->url, path, sizeof(path));
        char *data = NULL;
        int len = 0;
        int st = http_request(ip, HTTP_PORT, "GET", path, NULL, &data, &len, 8000);
        pthread_mutex_lock(&g_lock);
        if (st == 200 && data && len > 0) {
            job->data = data;
            job->len = len;
            job->state = 2;
        } else {
            free(data);
            job->state = 4;
        }
        pthread_mutex_unlock(&g_lock);
    }
    return NULL;
}

// ===================================================================== desenho
static SDL_Renderer *R;
static PlFontData g_font_data;

typedef struct {
    int size, bold;
    TTF_Font *f;
} FontEntry;
static FontEntry g_fonts[24];

static TTF_Font *font(int size, int bold)
{
    if (size < 8)
        size = 8;
    for (int i = 0; i < 24; i++)
        if (g_fonts[i].f && g_fonts[i].size == size && g_fonts[i].bold == bold)
            return g_fonts[i].f;
    for (int i = 0; i < 24; i++)
        if (!g_fonts[i].f) {
            SDL_RWops *rw = SDL_RWFromConstMem(g_font_data.address, g_font_data.size);
            g_fonts[i].f = TTF_OpenFontRW(rw, 1, size);
            g_fonts[i].size = size;
            g_fonts[i].bold = bold;
            TTF_SetFontStyle(g_fonts[i].f, TTF_STYLE_ITALIC | (bold ? TTF_STYLE_BOLD : 0));
            return g_fonts[i].f;
        }
    return g_fonts[0].f;
}

static void set_color(SDL_Color c) { SDL_SetRenderDrawColor(R, c.r, c.g, c.b, c.a); }
static SDL_Color rgba(int r, int g, int b, int a) { return (SDL_Color){r, g, b, a}; }

static void fill(int x, int y, int w, int h, SDL_Color c)
{
    SDL_Rect r = {x, y, w, h};
    set_color(c);
    SDL_RenderFillRect(R, &r);
}

static void outline(int x, int y, int w, int h, int t, SDL_Color c)
{
    fill(x, y, w, t, c);
    fill(x, y + h - t, w, t, c);
    fill(x, y + t, t, h - 2 * t, c);
    fill(x + w - t, y + t, t, h - 2 * t, c);
}

static void vgrad(int x, int y, int w, int h, SDL_Color a, SDL_Color b)
{
    for (int i = 0; i < h; i++) {
        float t = h > 1 ? (float)i / (h - 1) : 0;
        SDL_SetRenderDrawColor(R, a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t,
                               a.a + (b.a - a.a) * t);
        SDL_RenderDrawLine(R, x, y + i, x + w - 1, y + i);
    }
}

// texto: wrap=0 linha única. align: 0 esquerda, 1 centro, 2 direita. Retorna largura desenhada.
static int text(const char *s, int x, int y, int size, int bold, SDL_Color c, int align, int wrap, int shadow,
                int *out_h)
{
    if (!s || !s[0])
        return 0;
    TTF_Font *f = font(size, bold);
    SDL_Surface *sf = wrap > 0 ? TTF_RenderUTF8_Blended_Wrapped(f, s, c, wrap) : TTF_RenderUTF8_Blended(f, s, c);
    if (!sf)
        return 0;
    SDL_Texture *t = SDL_CreateTextureFromSurface(R, sf);
    int w = sf->w, h = sf->h;
    SDL_FreeSurface(sf);
    int dx = align == 1 ? x - w / 2 : align == 2 ? x - w : x;
    if (shadow) {
        SDL_SetTextureColorMod(t, 0, 0, 0);
        SDL_SetTextureAlphaMod(t, 220);
        for (int k = 0; k < 4; k++) {
            SDL_Rect sr = {dx + (k & 1 ? 2 : -1), y + (k & 2 ? 2 : -1), w, h};
            SDL_RenderCopy(R, t, NULL, &sr);
        }
        SDL_SetTextureColorMod(t, 255, 255, 255);
        SDL_SetTextureAlphaMod(t, 255);
    }
    SDL_Rect dr = {dx, y, w, h};
    SDL_RenderCopy(R, t, NULL, &dr);
    SDL_DestroyTexture(t);
    if (out_h)
        *out_h = h;
    return w;
}

static int text_w(const char *s, int size, int bold)
{
    int w = 0, h = 0;
    TTF_SizeUTF8(font(size, bold), s, &w, &h);
    return w;
}

// ---------------------------------------------------------- botões clicáveis (imediato)
typedef struct {
    SDL_Rect r;
    int id, arg;
} Btn;
#define BTN_MAX 64
static Btn g_btn[BTN_MAX];
static int g_nbtn = 0;
static int g_focus = -1;        // navegação por D-pad nos modais

static void add_btn(int x, int y, int w, int h, int id, int arg)
{
    if (g_nbtn < BTN_MAX)
        g_btn[g_nbtn++] = (Btn){{x, y, w, h}, id, arg};
}

static void button(const char *label, int x, int y, int w, int h, int id, int arg, int disabled, int size)
{
    int idx = g_nbtn;
    vgrad(x, y, w, h, disabled ? rgba(40, 39, 34, 255) : rgba(59, 58, 51, 255),
          disabled ? rgba(26, 25, 22, 255) : rgba(34, 33, 29, 255));
    outline(x, y, w, h, 1, idx == g_focus ? rgba(232, 220, 154, 255) : rgba(119, 117, 106, 255));
    int th = 0;
    TTF_SizeUTF8(font(size, 0), label, NULL, &th);
    text(label, x + w / 2, y + (h - th) / 2, size, 0, disabled ? rgba(109, 107, 98, 255) : rgba(236, 235, 227, 255),
         1, 0, 1, NULL);
    if (!disabled)
        add_btn(x, y, w, h, id, arg);
}

// ===================================================================== UI
enum { B_NONE, B_KEYS, B_CONFIG, B_MENU_OPT, B_CLOSE, B_DISC_YES, B_KEY, B_KEY_BACK, B_KEY_OK, B_LAYOUT, B_EXIT,
       B_DISCOVER };
enum { M_NONE, M_EXAMINE, M_DISCARD, M_LIST, M_CONFIG };
enum { OPT_EQUIP, OPT_USE, OPT_EXAMINE, OPT_DISCARD, OPT_CANCEL };

static int g_modal = M_NONE;
static int g_modal_slot = -1;
static int g_sel = -1;                     // item destacado
static int g_menu_open = 0, g_menu_slot = -1, g_menu_x = 0, g_menu_y = 0;
static char g_ipedit[64];

// geometria da maleta desenhada
static float g_cs = 96, g_ox = 0, g_oy = 0, g_pad = 0;
static void layout_case(int W, int H)
{
    float cs1 = SCR_W / (W + 1.2f), cs2 = (SCR_H - BAR_H - 8) / (H + 1.2f);
    g_cs = cs1 < cs2 ? cs1 : cs2;
    g_pad = g_cs * 0.6f;
    float tw = g_cs * (W + 1.2f), th = g_cs * (H + 1.2f);
    g_ox = (SCR_W - tw) / 2;
    g_oy = BAR_H + (SCR_H - BAR_H - th) / 2;
}
static float cell_x(float u) { return g_ox + g_pad + u * g_cs; }
static float cell_y(float v) { return g_oy + g_pad + v * g_cs; }

// arraste
typedef struct {
    int on, active, slot, rot, w, h, cellx, celly, ok, turns, has_ang;
    float grabx, graby, fx, fy, ang0;
    float sx, sy, px, py;
    u64 t0;
    Item it;
} Drag;
static Drag g_drag;

static Item *find_item(State *st, int slot)
{
    for (int i = 0; i < st->nitems; i++)
        if (st->items[i].slot == slot)
            return &st->items[i];
    return NULL;
}

static const char *type_name(int t)
{
    static const char *n[] = {"outro", "arma", "munição", "granada", "outro", "tesouro", "recuperação",
                              "item-chave", "bônus", "acessório", "arquivo", "mapa/maleta", "gema", "tampinha",
                              "importante"};
    return t >= 0 && t <= 14 ? n[t] : "item";
}

static int is_knife(int id) { return id == 13 || id == 56; }

static int fits(State *st, int except, int x, int y, int w, int h)
{
    if (x < 0 || y < 0 || x + w > st->W || y + h > st->H)
        return 0;
    for (int i = 0; i < st->nitems; i++) {
        Item *o = &st->items[i];
        if (o->slot == except)
            continue;
        if (x < o->x + o->w && x + w > o->x && y < o->y + o->h && y + h > o->y)
            return 0;
    }
    return 1;
}

static void draw_item(Item *it, float u, float v, int rot, int w, int h, int dragging, int selected)
{
    float s = g_cs / 96.0f;
    int x = (int)cell_x(u), y = (int)cell_y(v), W = (int)(w * g_cs), H = (int)(h * g_cs);
    int a = dragging ? 235 : 255;
    Icon *ic = icon_get(it->icon);
    if (ic && ic->state == 3) {
        int ow = (rot & 1) ? H : W, oh = (rot & 1) ? W : H;     // área na orientação padrão
        float k = fminf((float)ow / ic->tw, (float)oh / ic->th);
        int dw = ic->tw * k, dh = ic->th * k;
        SDL_Rect dr = {x + W / 2 - dw / 2, y + H / 2 - dh / 2, dw, dh};
        SDL_SetTextureAlphaMod(ic->tex, a);
        SDL_RenderCopyEx(R, ic->tex, NULL, &dr, (rot & 1) ? 90.0 : 0.0, NULL, SDL_FLIP_NONE);
    } else {
        int in = (int)(3 * s);
        fill(x + in, y + in, W - 2 * in, H - 2 * in, rgba(110, 110, 100, 82 * a / 255));
        outline(x + in, y + in, W - 2 * in, H - 2 * in, (int)fmaxf(1, 2 * s), rgba(255, 255, 255, 56 * a / 255));
        int th = 0, fs = (int)(22 * s);
        TTF_Font *f = font(fs, 0);
        SDL_Surface *sf = TTF_RenderUTF8_Blended_Wrapped(f, it->name, rgba(255, 255, 255, 255), W - (int)(14 * s));
        if (sf) {
            th = sf->h;
            SDL_FreeSurface(sf);
        }
        // centraliza cada linha do texto quebrado
        char buf[96];
        snprintf(buf, sizeof(buf), "%s", it->name);
        int lines = th / TTF_FontLineSkip(f);
        if (lines <= 1)
            text(buf, x + W / 2, y + (H - th) / 2, fs, 0, rgba(255, 255, 255, 255), 1, 0, 1, NULL);
        else {
            // quebra simples por palavras
            int ly = y + (H - th) / 2;
            char *save = NULL, line[96] = "";
            for (char *wd = strtok_r(buf, " ", &save); wd; wd = strtok_r(NULL, " ", &save)) {
                char trial[96];
                snprintf(trial, sizeof(trial), "%s%s%s", line, line[0] ? " " : "", wd);
                if (line[0] && text_w(trial, fs, 0) > W - (int)(14 * s)) {
                    text(line, x + W / 2, ly, fs, 0, rgba(255, 255, 255, 255), 1, 0, 1, NULL);
                    ly += TTF_FontLineSkip(f);
                    snprintf(line, sizeof(line), "%s", wd);
                } else
                    snprintf(line, sizeof(line), "%s", trial);
            }
            text(line, x + W / 2, ly, fs, 0, rgba(255, 255, 255, 255), 1, 0, 1, NULL);
        }
    }
    if (it->eq)
        text("E", x + (int)(18 * s), y + (int)(4 * s), (int)(58 * s), 1, rgba(239, 239, 232, 255), 0, 0, 1, NULL);
    if (it->count >= 0) {
        char n[16];
        snprintf(n, sizeof(n), "%d", it->count);
        int fs = (int)(52 * s), bh = (int)(66 * s), tw = text_w(n, fs, 0), bw = tw + (int)(14 * s);
        int bx = x + W - (int)(4 * s) - bw, by = y + H - (int)(6 * s) - bh;
        fill(bx, by, bw, bh, rgba(13, 13, 13, 255));
        int th = TTF_FontHeight(font(fs, 0));
        text(n, bx + bw - (int)(7 * s), by + (bh - th) / 2, fs, 0, rgba(232, 232, 224, 255), 2, 0, 0, NULL);
    }
    if (selected) {
        SDL_SetRenderDrawBlendMode(R, SDL_BLENDMODE_ADD);
        fill(x, y, W, H, rgba(64, 61, 36, 255));
        SDL_SetRenderDrawBlendMode(R, SDL_BLENDMODE_BLEND);
    }
}

static void draw_case(State *st)
{
    float s = g_cs / 96.0f;
    int fx = (int)(g_ox + g_pad * 0.35f), fy = (int)(g_oy + g_pad * 0.35f);
    int fw = (int)(g_cs * (st->W + 1.2f) - g_pad * 0.7f), fh = (int)(g_cs * (st->H + 1.2f) - g_pad * 0.7f);
    vgrad(fx, fy, fw, fh, rgba(156, 154, 144, 255), rgba(91, 90, 84, 255));
    outline(fx, fy, fw, fh, (int)(10 * s), rgba(58, 57, 53, 255));
    int bx = (int)cell_x(0), by = (int)cell_y(0), bw = (int)(st->W * g_cs), bh = (int)(st->H * g_cs);
    fill(bx, by, bw, bh, rgba(20, 20, 22, 255));
    for (int k = 0; k < 14; k++) {               // brilho radial aproximado
        float t = k / 14.0f;
        int ix = (int)(bw * t * 0.5f * 0.9f), iy = (int)(bh * t * 0.5f * 0.9f);
        fill(bx + ix, by + iy, bw - 2 * ix, bh - 2 * iy, rgba(38, 38, 42, 18));
    }
    for (int u = 0; u < st->W; u++)
        fill(bx + (int)(u * g_cs), by, 1, bh, rgba(190, 190, 180, 46));
    for (int v = 0; v < st->H; v++)
        fill(bx, by + (int)(v * g_cs), bw, 1, rgba(190, 190, 180, 46));

    for (int i = 0; i < st->nitems; i++) {
        Item *it = &st->items[i];
        if (g_drag.on && g_drag.active && g_drag.slot == it->slot)
            continue;
        draw_item(it, it->x, it->y, it->rot, it->w, it->h, 0, g_sel == it->slot);
    }
    if (g_drag.on && g_drag.active) {
        int gx = (int)cell_x(g_drag.cellx), gy = (int)cell_y(g_drag.celly);
        int gw = (int)(g_drag.w * g_cs), gh = (int)(g_drag.h * g_cs);
        SDL_Color c = g_drag.ok ? rgba(110, 230, 120, 217) : rgba(235, 80, 70, 230);
        fill(gx, gy, gw, gh, rgba(c.r, c.g, c.b, 40));
        outline(gx, gy, gw, gh, (int)fmaxf(2, 6 * s), c);
        Item t = g_drag.it;
        t.rot = g_drag.rot;
        draw_item(&t, g_drag.fx, g_drag.fy, g_drag.rot, g_drag.w, g_drag.h, 1, 0);
    }
}

static void draw_bar(State *st, int net_ok)
{
    vgrad(0, 0, SCR_W, BAR_H, rgba(26, 25, 22, 255), rgba(8, 8, 7, 255));
    SDL_Color dot = !net_ok || !st->connected ? rgba(192, 57, 43, 255)
                    : st->running             ? rgba(46, 204, 113, 255)
                                              : rgba(229, 179, 53, 255);
    fill(18, BAR_H / 2 - 6, 12, 12, dot);
    const char *msg = g_discovering           ? "Procurando o PC na rede…"
                      : !g_ip[0]               ? "PC não encontrado — toque em Config."
                      : !net_ok               ? "Conectando ao PC…"
                      : !st->connected        ? "Jogo não conectado"
                      : st->running           ? "ao vivo"
                                              : "pausado (janela do RE4 sem foco)";
    text(msg, 40, BAR_H / 2 - 13, 22, 0, rgba(200, 198, 186, 255), 0, 0, 0, NULL);
    char ipl[96];
    snprintf(ipl, sizeof(ipl), "PC: %s", g_ip[0] ? g_ip : "—");
    text(ipl, SCR_W / 2, BAR_H / 2 - 13, 20, 0, rgba(140, 138, 126, 255), 1, 0, 0, NULL);
    button("Config.", SCR_W - 140, 8, 128, BAR_H - 16, B_CONFIG, 0, 0, 20);
    button("Keys / Treasures", SCR_W - 140 - 220, 8, 210, BAR_H - 16, B_KEYS, 0, 0, 20);
}

static int menu_options(Item *it, int *ids, const char **labels, int *disabled)
{
    int n = 0;
    if (it->type == 1 || it->type == 3) {          // armas e granadas
        ids[n] = OPT_EQUIP, labels[n] = it->eq ? "Equipped" : "Equip", disabled[n++] = it->eq;
    }
    if (it->type == 6)
        ids[n] = OPT_USE, labels[n] = "Use", disabled[n++] = 0;
    ids[n] = OPT_EXAMINE, labels[n] = "Examine", disabled[n++] = 0;
    ids[n] = OPT_DISCARD, labels[n] = "Discard", disabled[n++] = it->type == 7 || it->type == 14 || it->eq;
    ids[n] = OPT_CANCEL, labels[n] = "Cancel", disabled[n++] = 0;
    return n;
}

static void draw_menu(State *st)
{
    Item *it = find_item(st, g_menu_slot);
    if (!it) {
        g_menu_open = 0;
        return;
    }
    int ids[6], dis[6];
    const char *lab[6];
    int n = menu_options(it, ids, lab, dis);
    int w = 210, rh = 44, h = n * rh + 12;
    int x = g_menu_x + 16, y = g_menu_y - h / 2;
    if (x + w > SCR_W - 6)
        x = g_menu_x - w - 16;
    if (y < BAR_H + 4)
        y = BAR_H + 4;
    if (y + h > SCR_H - 6)
        y = SCR_H - 6 - h;
    vgrad(x, y, w, h, rgba(38, 38, 34, 245), rgba(18, 18, 16, 245));
    outline(x, y, w, h, 1, rgba(119, 117, 106, 255));
    int first = -1;
    for (int i = 0; i < n; i++)
        if (!dis[i] && first < 0)
            first = i;
    for (int i = 0; i < n; i++) {
        int ry = y + 6 + i * rh;
        if (i == first) {
            vgrad(x + 1, ry, w - 2, rh, rgba(110, 106, 70, 255), rgba(40, 39, 30, 255));
            fill(x + 10, ry + rh / 2 - 6, 7, 12, rgba(232, 220, 154, 255));
        }
        text(lab[i], x + 28, ry + 8, 24, 0, dis[i] ? rgba(109, 107, 98, 255) : rgba(236, 235, 227, 255), 0, 0, 1,
             NULL);
        if (!dis[i])
            add_btn(x, ry, w, rh, B_MENU_OPT, ids[i]);
    }
}

static void panel(int w, int h, int *px, int *py)
{
    fill(0, 0, SCR_W, SCR_H, rgba(0, 0, 0, 215));
    int x = (SCR_W - w) / 2, y = (SCR_H - h) / 2;
    vgrad(x, y, w, h, rgba(37, 36, 31, 255), rgba(21, 21, 18, 255));
    outline(x, y, w, h, 1, rgba(119, 117, 106, 255));
    *px = x;
    *py = y;
}

static void pips(int x, int y, int n)
{
    for (int i = 0; i < 5; i++) {
        fill(x + i * 20, y, 15, 15, i < n ? rgba(216, 201, 122, 255) : rgba(58, 56, 48, 255));
        outline(x + i * 20, y, 15, 15, 1, rgba(87, 85, 74, 255));
    }
}

static void draw_modal(State *st)
{
    int x, y;
    Item *it = find_item(st, g_modal_slot);
    if ((g_modal == M_EXAMINE || g_modal == M_DISCARD) && !it) {
        g_modal = M_NONE;
        return;
    }
    if (g_modal == M_EXAMINE) {
        panel(640, 430, &x, &y);
        Icon *ic = icon_get(it->icon);
        int ty = y + 22;
        if (ic && ic->state == 3) {
            float k = fminf(560.0f / ic->tw, 150.0f / ic->th);
            SDL_Rect dr = {x + 320 - (int)(ic->tw * k) / 2, ty, (int)(ic->tw * k), (int)(ic->th * k)};
            SDL_SetTextureAlphaMod(ic->tex, 255);
            SDL_RenderCopy(R, ic->tex, NULL, &dr);
            ty += 160;
        }
        text(it->name, x + 24, ty, 36, 0, rgba(236, 235, 227, 255), 0, 0, 1, NULL);
        text(type_name(it->type), x + 24, ty + 46, 20, 0, rgba(156, 154, 140, 255), 0, 0, 0, NULL);
        int ly = ty + 84;
        char b[64];
        if (it->type == 1) {
            if (!is_knife(it->id)) {
                snprintf(b, sizeof(b), "%d / %d", it->ammo, it->ammoMax);
                text("Ammo", x + 24, ly, 22, 0, rgba(156, 154, 140, 255), 0, 0, 0, NULL);
                text(b, x + 230, ly, 22, 0, rgba(236, 235, 227, 255), 0, 0, 0, NULL);
                ly += 34;
            }
            const char *ln[] = {"Firepower", "Firing speed", "Reload speed", "Capacity"};
            int vals[] = {it->fp, it->fs, it->rs, it->cap};
            for (int i = 0; i < 4; i++, ly += 32) {
                text(ln[i], x + 24, ly, 22, 0, rgba(156, 154, 140, 255), 0, 0, 0, NULL);
                pips(x + 230, ly + 6, vals[i]);
            }
        } else if (it->num > 1 || it->max > 1) {
            snprintf(b, sizeof(b), it->max > 1 ? "%d / %d" : "%d", it->num, it->max);
            text("Quantidade", x + 24, ly, 22, 0, rgba(156, 154, 140, 255), 0, 0, 0, NULL);
            text(b, x + 230, ly, 22, 0, rgba(236, 235, 227, 255), 0, 0, 0, NULL);
        }
        button("Back", x + 24, y + 430 - 70, 592, 50, B_CLOSE, 0, 0, 24);
    } else if (g_modal == M_DISCARD) {
        panel(560, 220, &x, &y);
        char b[160];
        snprintf(b, sizeof(b), "Discard %s?", it->name);
        text(b, x + 24, y + 24, 30, 0, rgba(236, 235, 227, 255), 0, 512, 1, NULL);
        text("O item será removido do inventário.", x + 24, y + 90, 20, 0, rgba(156, 154, 140, 255), 0, 0, 0, NULL);
        button("Yes", x + 24, y + 140, 250, 54, B_DISC_YES, 0, 0, 24);
        button("No", x + 286, y + 140, 250, 54, B_CLOSE, 0, 0, 24);
    } else if (g_modal == M_LIST) {
        panel(900, 600, &x, &y);
        text("Keys / Treasures", x + 24, y + 18, 34, 0, rgba(236, 235, 227, 255), 0, 0, 1, NULL);
        const char *gname[] = {"Keys", "Treasures", "Files"};
        int ly = y + 72;
        for (int g = 0; g < 3; g++) {
            int any = 0;
            for (int i = 0; i < st->nothers; i++) {
                Other *o = &st->others[i];
                int grp = (o->type == 5 || o->type == 12 || o->type == 13) ? 1 : o->type == 10 ? 2 : 0;
                if (grp != g)
                    continue;
                if (!any) {
                    text(gname[g], x + 24, ly, 20, 0, rgba(156, 154, 140, 255), 0, 0, 0, NULL);
                    ly += 30;
                    any = 1;
                }
                char b[128];
                snprintf(b, sizeof(b), o->num > 1 ? "%s  ×%d" : "%s", o->name, o->num);
                if (ly < y + 600 - 90)
                    text(b, x + 40, ly, 22, 0, rgba(236, 235, 227, 255), 0, 0, 0, NULL);
                ly += 30;
            }
            ly += 8;
        }
        button("Back", x + 24, y + 600 - 70, 852, 50, B_CLOSE, 0, 0, 24);
    } else if (g_modal == M_CONFIG) {
        panel(760, 620, &x, &y);
        text("Conexão com o PC", x + 24, y + 18, 32, 0, rgba(236, 235, 227, 255), 0, 0, 1, NULL);
        text("IP do PC que roda o servidor (aparece no console do iniciar.bat):", x + 24, y + 64, 18, 0,
             rgba(156, 154, 140, 255), 0, 0, 0, NULL);
        fill(x + 24, y + 94, 712, 58, rgba(13, 13, 11, 255));
        outline(x + 24, y + 94, 712, 58, 1, rgba(119, 117, 106, 255));
        char shown[80];
        snprintf(shown, sizeof(shown), "%s%s", g_ipedit, (now_ms() / 500) % 2 ? "|" : " ");
        text(shown, x + 40, y + 104, 32, 0, rgba(236, 235, 227, 255), 0, 0, 0, NULL);
        const char *keys[] = {"1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "0", "⌫"};
        int kw = 150, kh = 62, kx = x + 24, ky = y + 168;
        for (int i = 0; i < 12; i++) {
            int c = i % 3, r = i / 3;
            button(keys[i], kx + c * (kw + 10), ky + r * (kh + 10), kw, kh, i == 11 ? B_KEY_BACK : B_KEY, keys[i][0],
                   0, 30);
        }
        int rx = x + 24 + 3 * (kw + 10) + 16, rw = 736 - (rx - x);
        button("Conectar", rx, ky, rw, kh, B_KEY_OK, 0, 0, 26);
        button(g_layout ? "Botões: rótulos Nintendo" : "Botões: posição Xbox", rx, ky + kh + 10, rw, kh, B_LAYOUT, 0,
               0, 20);
        button("Cancelar", rx, ky + 2 * (kh + 10), rw, kh, B_CLOSE, 0, 0, 24);
        button("Sair do app", rx, ky + 3 * (kh + 10), rw, kh, B_EXIT, 0, 0, 22);
        button(g_discovering ? "Procurando…" : "Procurar o PC automaticamente", x + 24, ky + 4 * (kh + 10) + 6, 712,
               kh - 6, B_DISCOVER, 0, g_discovering, 22);
        char me[96];
        u32 hid = (u32)gethostid();
        snprintf(me, sizeof(me), "Este Switch: %u.%u.%u.%u   •   portas 8044 (maleta) e 8045 (controle), automáticas",
                 hid & 0xFF, (hid >> 8) & 0xFF, (hid >> 16) & 0xFF, hid >> 24);
        text(me, x + 24, y + 620 - 62, 17, 0, rgba(156, 154, 140, 255), 0, 0, 0, NULL);
        text("Toque ou use o D-pad + A.  B volta.", x + 24, y + 620 - 36, 17, 0, rgba(156, 154, 140, 255), 0, 0, 0,
             NULL);
    }
}

static void draw_toast(void)
{
    char t[256];
    pthread_mutex_lock(&g_lock);
    int show = now_ms() < g_toast_until;
    snprintf(t, sizeof(t), "%s", g_toast);
    pthread_mutex_unlock(&g_lock);
    if (!show)
        return;
    int w = text_w(t, 22, 0) + 36;
    int x = (SCR_W - w) / 2, y = SCR_H - 64;
    fill(x, y, w, 46, rgba(20, 20, 18, 242));
    outline(x, y, w, 46, 1, rgba(119, 117, 106, 255));
    text(t, SCR_W / 2, y + 9, 22, 0, rgba(236, 235, 227, 255), 1, 0, 0, NULL);
}

// ===================================================================== ações da UI
static void open_config(void)
{
    g_modal = M_CONFIG;
    snprintf(g_ipedit, sizeof(g_ipedit), "%s", g_ip);
    g_focus = -1;
}

static void apply_ip(void)
{
    if (!g_ipedit[0]) {
        toast("Digite o IP do PC");
        return;
    }
    pthread_mutex_lock(&g_lock);
    snprintf(g_ip, sizeof(g_ip), "%s", g_ipedit);
    g_ip_gen++;
    g_net_ok = 0;
    memset(&g_state, 0, sizeof(g_state));
    pthread_mutex_unlock(&g_lock);
    cfg_save();
    pad_open(g_ip, PAD_PORT);
    g_modal = M_NONE;
    toast("Conectando…");
}

static void do_option(State *st, int opt)
{
    Item *it = find_item(st, g_menu_slot);
    g_menu_open = 0;
    if (!it)
        return;
    char body[96], label[160];
    snprintf(body, sizeof(body), "{\"slot\":%d}", it->slot);
    switch (opt) {
    case OPT_EQUIP:
        snprintf(label, sizeof(label), "Equipped: %s", it->name);
        post_action("/api/equip", body, label);
        break;
    case OPT_USE:
        snprintf(label, sizeof(label), "Used: %s", it->name);
        post_action("/api/use", body, label);
        break;
    case OPT_EXAMINE:
        g_modal = M_EXAMINE;
        g_modal_slot = it->slot;
        g_focus = -1;
        break;
    case OPT_DISCARD:
        g_modal = M_DISCARD;
        g_modal_slot = it->slot;
        g_focus = -1;
        break;
    default:
        g_sel = -1;
    }
}

static void press_button(State *st, Btn *b)
{
    switch (b->id) {
    case B_KEYS: g_modal = M_LIST; g_focus = -1; break;
    case B_CONFIG: open_config(); break;
    case B_MENU_OPT: do_option(st, b->arg); break;
    case B_CLOSE: g_modal = M_NONE; break;
    case B_DISC_YES: {
        Item *it = find_item(st, g_modal_slot);
        if (it) {
            char body[64], label[160];
            snprintf(body, sizeof(body), "{\"slot\":%d}", it->slot);
            snprintf(label, sizeof(label), "Discarded: %s", it->name);
            post_action("/api/discard", body, label);
        }
        g_modal = M_NONE;
        g_sel = -1;
        break;
    }
    case B_KEY: {
        int l = strlen(g_ipedit);
        if (l < 40) {
            g_ipedit[l] = (char)b->arg;
            g_ipedit[l + 1] = 0;
        }
        break;
    }
    case B_KEY_BACK: {
        int l = strlen(g_ipedit);
        if (l)
            g_ipedit[l - 1] = 0;
        break;
    }
    case B_KEY_OK: apply_ip(); break;
    case B_LAYOUT: g_layout = !g_layout; cfg_save(); break;
    case B_EXIT: g_quit = 1; break;
    case B_DISCOVER: g_discover_now = 1; g_modal = M_NONE; break;
    }
}

static Btn *hit_btn(int x, int y)
{
    for (int i = g_nbtn - 1; i >= 0; i--) {
        SDL_Rect r = g_btn[i].r;
        if (x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h)
            return &g_btn[i];
    }
    return NULL;
}

// navegação por D-pad entre os botões do modal
static void focus_move(int dx, int dy)
{
    if (!g_nbtn)
        return;
    if (g_focus < 0 || g_focus >= g_nbtn) {
        g_focus = 0;
        return;
    }
    SDL_Rect c = g_btn[g_focus].r;
    float cx = c.x + c.w / 2.0f, cy = c.y + c.h / 2.0f, best = 1e9;
    int bi = g_focus;
    for (int i = 0; i < g_nbtn; i++) {
        if (i == g_focus)
            continue;
        SDL_Rect r = g_btn[i].r;
        float ex = r.x + r.w / 2.0f - cx, ey = r.y + r.h / 2.0f - cy;
        float along = ex * dx + ey * dy;
        if (along <= 4)
            continue;
        float d = along + 2.5f * fabsf(ex * dy + ey * dx);
        if (d < best)
            best = d, bi = i;
    }
    g_focus = bi;
}

// ===================================================================== toque
typedef struct {
    int down, id;
    float x, y;
} Finger;

static void begin_drag(State *st)
{
    Item *it = find_item(st, g_drag.slot);
    if (!it) {
        g_drag.on = 0;
        return;
    }
    g_drag.active = 1;
    g_menu_open = 0;
    g_sel = -1;
}

static void update_drag(State *st, float px, float py)
{
    g_drag.px = px, g_drag.py = py;
    float u = (px - cell_x(0)) / g_cs, v = (py - cell_y(0)) / g_cs;
    g_drag.fx = u - g_drag.grabx;
    g_drag.fy = v - g_drag.graby;
    g_drag.cellx = (int)lroundf(g_drag.fx);
    g_drag.celly = (int)lroundf(g_drag.fy);
    g_drag.ok = fits(st, g_drag.slot, g_drag.cellx, g_drag.celly, g_drag.w, g_drag.h);
}

static void rotate_drag(State *st)
{
    g_drag.rot ^= 1;
    int t = g_drag.w;
    g_drag.w = g_drag.h;
    g_drag.h = t;
    float gx = g_drag.graby, gy = g_drag.grabx;
    g_drag.grabx = fminf(gx, g_drag.w - 0.5f);
    g_drag.graby = fminf(gy, g_drag.h - 0.5f);
    update_drag(st, g_drag.px, g_drag.py);
}

static void finish_drag(State *st)
{
    Item *it = find_item(st, g_drag.slot);
    if (it && g_drag.ok && (g_drag.cellx != it->x || g_drag.celly != it->y || g_drag.rot != it->rot)) {
        char body[128];
        snprintf(body, sizeof(body), "{\"slot\":%d,\"x\":%d,\"y\":%d,\"rot\":%d}", it->slot, g_drag.cellx,
                 g_drag.celly, g_drag.rot);
        post_action("/api/move", body, NULL);
        // atualiza local para não "piscar" até o servidor responder
        it->x = g_drag.cellx, it->y = g_drag.celly, it->rot = g_drag.rot, it->w = g_drag.w, it->h = g_drag.h;
    }
    g_drag.on = 0;
}

static void handle_touch(State *st, HidTouchScreenState *ts, Finger *fing, int *nf_prev)
{
    int n = ts->count;
    float x0 = n ? ts->touches[0].x : 0, y0 = n ? ts->touches[0].y : 0;
    u64 t = now_ms();

    if (n >= 1 && *nf_prev == 0) {                                       // novo toque
        fing[0] = (Finger){1, ts->touches[0].finger_id, x0, y0};
        Btn *b = hit_btn((int)x0, (int)y0);
        if (g_modal != M_NONE || (b && b->id != B_NONE) || (g_menu_open && b)) {
            // botões/modais tratam no soltar
        } else if (g_menu_open) {
            g_menu_open = 0;
            g_sel = -1;
        }
        if (g_modal == M_NONE && !(b && (b->id == B_MENU_OPT || b->id == B_KEYS || b->id == B_CONFIG)) &&
            st->connected) {
            float u = (x0 - cell_x(0)) / g_cs, v = (y0 - cell_y(0)) / g_cs;
            for (int i = 0; i < st->nitems; i++) {
                Item *it = &st->items[i];
                if (u >= it->x && u < it->x + it->w && v >= it->y && v < it->y + it->h) {
                    g_drag = (Drag){0};
                    g_drag.on = 1, g_drag.slot = it->slot, g_drag.rot = it->rot, g_drag.w = it->w,
                    g_drag.h = it->h;
                    g_drag.grabx = u - it->x, g_drag.graby = v - it->y, g_drag.fx = it->x, g_drag.fy = it->y;
                    g_drag.sx = x0, g_drag.sy = y0, g_drag.px = x0, g_drag.py = y0, g_drag.t0 = t;
                    g_drag.it = *it;
                    break;
                }
            }
            if (!g_drag.on && g_sel >= 0)
                g_sel = -1;
        }
    } else if (n >= 1) {                                                 // movendo
        if (g_drag.on && !g_drag.active) {
            if (hypotf(x0 - g_drag.sx, y0 - g_drag.sy) > 18)
                g_drag.on = 0;                                           // virou outro gesto
            else if (t - g_drag.t0 > 320)
                begin_drag(st);
        }
        if (g_drag.on && g_drag.active) {
            if (n >= 2) {
                float a = atan2f(ts->touches[1].y - ts->touches[0].y, ts->touches[1].x - ts->touches[0].x);
                if (!g_drag.has_ang) {
                    g_drag.has_ang = 1, g_drag.ang0 = a, g_drag.turns = 0;
                } else {
                    float d = a - g_drag.ang0;
                    d = atan2f(sinf(d), cosf(d));
                    int steps = (int)lroundf(d / (float)(M_PI / 2));      // limiar de 45°
                    if (steps != g_drag.turns) {
                        if ((steps - g_drag.turns) % 2)
                            rotate_drag(st);
                        g_drag.turns = steps;
                    }
                }
            } else {
                g_drag.has_ang = 0;
                update_drag(st, x0, y0);
            }
        }
        fing[0].x = x0, fing[0].y = y0;
    } else if (n == 0 && *nf_prev > 0) {                                 // soltou
        float x = fing[0].x, y = fing[0].y;
        if (g_drag.on && g_drag.active)
            finish_drag(st);
        else if (g_drag.on) {
            g_drag.on = 0;
            g_menu_open = 1, g_menu_slot = g_drag.slot, g_menu_x = (int)x, g_menu_y = (int)y;
            g_sel = g_drag.slot;
        } else {
            Btn *b = hit_btn((int)x, (int)y);
            if (b)
                press_button(st, b);
        }
    }
    *nf_prev = n;
}

// ===================================================================== controle → PC
static void send_pad(PadState *pad, int capture)
{
    static u32 seq = 0;
    u64 k = padGetButtons(pad);
    HidAnalogStickState l = padGetStickPos(pad, 0), r = padGetStickPos(pad, 1);
    u16 b = 0;
    if (!capture) {
        if (g_layout == 0) {         // posição (como no controle Xbox): baixo=A, direita=B, esquerda=X, cima=Y
            if (k & HidNpadButton_B) b |= XI_A;
            if (k & HidNpadButton_A) b |= XI_B;
            if (k & HidNpadButton_Y) b |= XI_X;
            if (k & HidNpadButton_X) b |= XI_Y;
        } else {                     // rótulos iguais
            if (k & HidNpadButton_A) b |= XI_A;
            if (k & HidNpadButton_B) b |= XI_B;
            if (k & HidNpadButton_X) b |= XI_X;
            if (k & HidNpadButton_Y) b |= XI_Y;
        }
        if (k & HidNpadButton_L) b |= XI_LB;
        if (k & HidNpadButton_R) b |= XI_RB;
        if (k & HidNpadButton_Plus) b |= XI_START;
        if (k & HidNpadButton_Minus) b |= XI_BACK;
        if (k & HidNpadButton_StickL) b |= XI_LS;
        if (k & HidNpadButton_StickR) b |= XI_RS;
        if (k & HidNpadButton_Up) b |= XI_UP;
        if (k & HidNpadButton_Down) b |= XI_DOWN;
        if (k & HidNpadButton_Left) b |= XI_LEFT;
        if (k & HidNpadButton_Right) b |= XI_RIGHT;
    }
    int on = !capture;
    pad_send(++seq, b, on && (k & HidNpadButton_ZL) ? 255 : 0, on && (k & HidNpadButton_ZR) ? 255 : 0,
             on ? (s16)l.x : 0, on ? (s16)l.y : 0, on ? (s16)r.x : 0, on ? (s16)r.y : 0);
}

static HidVibrationDeviceHandle g_vib[3][2];
static void vib_init(void)
{
    hidInitializeVibrationDevices(g_vib[0], 2, HidNpadIdType_Handheld, HidNpadStyleTag_NpadHandheld);
    hidInitializeVibrationDevices(g_vib[1], 2, HidNpadIdType_No1, HidNpadStyleTag_NpadJoyDual);
    hidInitializeVibrationDevices(g_vib[2], 1, HidNpadIdType_No1, HidNpadStyleTag_NpadFullKey);
}
static void vib_set(u16 left, u16 right)
{
    HidVibrationValue v[2];
    for (int i = 0; i < 2; i++) {
        v[i].amp_low = left / 65535.0f * 0.9f;
        v[i].freq_low = 160.0f;
        v[i].amp_high = right / 65535.0f * 0.9f;
        v[i].freq_high = 320.0f;
    }
    hidSendVibrationValues(g_vib[0], v, 2);
    hidSendVibrationValues(g_vib[1], v, 2);
    hidSendVibrationValues(g_vib[2], v, 1);
}

// ===================================================================== main
int main(int argc, char **argv)
{
    (void)argc, (void)argv;
    socketInitializeDefault();
    plInitialize(PlServiceType_User);
    plGetSharedFontByType(&g_font_data, PlSharedFontType_Standard);
    appletSetMediaPlaybackState(true);      // não deixa a tela apagar/dormir

    padConfigureInput(1, HidNpadStyleSet_NpadStandard);
    PadState pad;
    padInitializeDefault(&pad);
    hidInitializeTouchScreen();
    vib_init();

    SDL_Init(SDL_INIT_VIDEO);
    IMG_Init(IMG_INIT_PNG | IMG_INIT_JPG | IMG_INIT_WEBP);
    TTF_Init();
    SDL_Window *win = SDL_CreateWindow("RE4 Inventory", 0, 0, SCR_W, SCR_H, 0);
    R = SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    SDL_SetRenderDrawBlendMode(R, SDL_BLENDMODE_BLEND);

    cfg_load();
    if (g_ip[0])
        pad_open(g_ip, PAD_PORT);       // sem IP salvo: a thread de estado procura o PC na rede

    pthread_t th_state, th_act, th_icon;
    pthread_create(&th_state, NULL, state_thread, NULL);
    pthread_create(&th_act, NULL, action_thread, NULL);
    pthread_create(&th_icon, NULL, icon_thread, NULL);

    Finger fing[2] = {0};
    int nf_prev = 0;
    u16 rl = 0, rr = 0;
    State *st = malloc(sizeof(State));

    while (appletMainLoop() && !g_quit) {
        padUpdate(&pad);
        u64 down = padGetButtonsDown(&pad);

        pthread_mutex_lock(&g_lock);
        *st = g_state;
        int net_ok = g_net_ok;
        pthread_mutex_unlock(&g_lock);

        // ícones baixados -> texturas
        for (int i = 0; i < ICON_MAX; i++) {
            Icon *ic = &g_icons[i];
            if (ic->state == 2) {
                SDL_Surface *sf = IMG_Load_RW(SDL_RWFromConstMem(ic->data, ic->len), 1);
                pthread_mutex_lock(&g_lock);
                if (sf) {
                    ic->tex = SDL_CreateTextureFromSurface(R, sf);
                    ic->tw = sf->w, ic->th = sf->h;
                    SDL_FreeSurface(sf);
                    ic->state = 3;
                } else
                    ic->state = 4;
                free(ic->data);
                ic->data = NULL;
                pthread_mutex_unlock(&g_lock);
            }
        }

        // controle: com um modal aberto, os botões navegam o modal (o Leon fica parado)
        if (g_pad_reopen) {
            g_pad_reopen = 0;
            char ip[64];
            ip_copy(ip, sizeof(ip));
            pad_open(ip, PAD_PORT);
        }
        int capture = g_modal != M_NONE;
        send_pad(&pad, capture);
        if (pad_poll_rumble(&rl, &rr))
            vib_set(capture ? 0 : rl, capture ? 0 : rr);

        // toque (usa os botões desenhados no frame anterior)
        HidTouchScreenState ts = {0};
        if (hidGetTouchScreenStates(&ts, 1))
            handle_touch(st, &ts, fing, &nf_prev);
        if (capture) {
            if (down & HidNpadButton_AnyUp) focus_move(0, -1);
            if (down & HidNpadButton_AnyDown) focus_move(0, 1);
            if (down & HidNpadButton_AnyLeft) focus_move(-1, 0);
            if (down & HidNpadButton_AnyRight) focus_move(1, 0);
            if ((down & HidNpadButton_A) && g_focus >= 0 && g_focus < g_nbtn)
                press_button(st, &g_btn[g_focus]);
            if (down & HidNpadButton_B) {
                if (g_modal == M_CONFIG && g_ipedit[0] && strcmp(g_ipedit, g_ip)) {
                    int l = strlen(g_ipedit);
                    g_ipedit[l - 1] = 0;
                } else if (g_ip[0] || g_modal != M_CONFIG)
                    g_modal = M_NONE;
            }
        }

        // ---------------- desenho
        g_nbtn = 0;
        SDL_SetRenderDrawColor(R, 0, 0, 0, 255);
        SDL_RenderClear(R);
        if (st->valid && st->connected && st->W > 0) {
            layout_case(st->W, st->H);
            draw_case(st);
        } else {
            const char *m = g_discovering ? "Procurando o PC na rede…"
                            : !g_ip[0] ? "PC não encontrado. Abra o iniciar.bat no PC ou toque em Config."
                            : !net_ok ? "Sem resposta do PC — procurando de novo…"
                                      : (st->error[0] ? st->error : "Abra o Resident Evil 4 no PC");
            text(m, SCR_W / 2, SCR_H / 2 - 16, 28, 0, rgba(230, 223, 176, 255), 1, 0, 0, NULL);
        }
        draw_bar(st, net_ok);
        if (g_menu_open && g_modal == M_NONE)
            draw_menu(st);
        if (g_modal != M_NONE) {
            g_nbtn = 0;                     // só os botões do modal ficam ativos
            draw_modal(st);
        }
        draw_toast();
        SDL_RenderPresent(R);
    }

    g_quit = 1;
    pthread_cond_broadcast(&g_qcond);
    pad_send(0, 0, 0, 0, 0, 0, 0, 0);       // solta tudo no PC
    vib_set(0, 0);
    pad_close();
    free(st);
    SDL_DestroyRenderer(R);
    SDL_DestroyWindow(win);
    TTF_Quit();
    IMG_Quit();
    SDL_Quit();
    appletSetMediaPlaybackState(false);
    plExit();
    socketExit();
    return 0;
}
