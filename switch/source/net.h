#pragma once
#include <stdint.h>

// Requisição HTTP/1.1 simples (Connection: close). Retorna o status HTTP ou -1 em erro de rede.
// *out recebe o corpo (alocado com malloc, terminado em '\0'); o chamador libera.
int http_request(const char *ip, int port, const char *method, const char *path,
                 const char *body, char **out, int *out_len, int timeout_ms);

// Controle por UDP
int  pad_open(const char *ip, int port);
void pad_close(void);
void pad_send(uint32_t seq, uint16_t buttons, uint8_t lt, uint8_t rt,
              int16_t lx, int16_t ly, int16_t rx, int16_t ry);
// Lê respostas pendentes; retorna 1 se recebeu vibração nova.
int  pad_poll_rumble(uint16_t *left, uint16_t *right);

// Procura o servidor na rede local (broadcast UDP "RE4?" na porta do controle).
// Retorna 0 e preenche ip_out com o IP do PC que respondeu, ou -1.
int discover_server(int pad_port, char *ip_out, int n, int timeout_ms);
