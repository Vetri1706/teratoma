#include "aegis_queue.h"
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

struct aegis_queue {
    _Atomic size_t head;
    _Atomic size_t tail;
    size_t slots;
    size_t stride;
    size_t max_payload;
    unsigned char *storage;
};

void *aegis_queue_create(size_t capacity, size_t max_payload) {
    if (capacity == 0 || capacity == SIZE_MAX || max_payload == 0 ||
        max_payload > INT64_MAX || max_payload > SIZE_MAX - sizeof(size_t)) return NULL;
    size_t slots = capacity + 1;
    size_t stride = sizeof(size_t) + max_payload;
    if (slots > SIZE_MAX / stride) return NULL;
    struct aegis_queue *q = calloc(1, sizeof(*q));
    if (q == NULL) return NULL;
    atomic_init(&q->head, 0);
    atomic_init(&q->tail, 0);
    if (!atomic_is_lock_free(&q->head) || !atomic_is_lock_free(&q->tail)) {
        free(q);
        return NULL;
    }
    q->storage = malloc(slots * stride);
    if (q->storage == NULL) { free(q); return NULL; }
    q->slots = slots;
    q->stride = stride;
    q->max_payload = max_payload;
    return q;
}

int aegis_queue_push(void *queue, const void *data, size_t length) {
    struct aegis_queue *q = queue;
    if (q == NULL || data == NULL || length == 0 || length > q->max_payload) return -1;
    size_t head = atomic_load_explicit(&q->head, memory_order_relaxed);
    size_t next = head + 1 == q->slots ? 0 : head + 1;
    if (next == atomic_load_explicit(&q->tail, memory_order_acquire)) return 0;
    unsigned char *slot = q->storage + head * q->stride;
    memcpy(slot, &length, sizeof(length));
    memcpy(slot + sizeof(length), data, length);
    atomic_store_explicit(&q->head, next, memory_order_release);
    return 1;
}

int64_t aegis_queue_pop(void *queue, void *buffer, size_t capacity) {
    struct aegis_queue *q = queue;
    if (q == NULL || buffer == NULL) return -1;
    size_t tail = atomic_load_explicit(&q->tail, memory_order_relaxed);
    if (tail == atomic_load_explicit(&q->head, memory_order_acquire)) return 0;
    unsigned char *slot = q->storage + tail * q->stride;
    size_t length;
    memcpy(&length, slot, sizeof(length));
    if (length == 0 || length > q->max_payload || length > capacity) return -1;
    memcpy(buffer, slot + sizeof(length), length);
    size_t next = tail + 1 == q->slots ? 0 : tail + 1;
    atomic_store_explicit(&q->tail, next, memory_order_release);
    return (int64_t)length;
}

void aegis_queue_destroy(void *queue) {
    struct aegis_queue *q = queue;
    if (q != NULL) { free(q->storage); free(q); }
}
