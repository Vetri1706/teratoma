#ifndef AEGIS_QUEUE_H
#define AEGIS_QUEUE_H
#include <stddef.h>
#include <stdint.h>
/* One fixed producer and one fixed consumer; join owners before destroy. */
void *aegis_queue_create(size_t capacity, size_t max_payload);
int aegis_queue_push(void *queue, const void *data, size_t length);
int64_t aegis_queue_pop(void *queue, void *buffer, size_t capacity);
void aegis_queue_destroy(void *queue);
#endif
