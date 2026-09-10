#include "aegis_queue.h"
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHANNELS 4
struct message { uint64_t sequence, channel, checksum; unsigned char bytes[64]; };
struct channel { void *queue; uint64_t id, count, full_spins, empty_spins; };
static _Atomic int failed;

static uint64_t checksum(const struct message *message) {
    uint64_t hash = UINT64_C(1469598103934665603);
    hash = (hash ^ message->sequence) * UINT64_C(1099511628211);
    hash = (hash ^ message->channel) * UINT64_C(1099511628211);
    for (size_t i = 0; i < sizeof(message->bytes); ++i) hash = (hash ^ message->bytes[i]) * UINT64_C(1099511628211);
    return hash;
}

static void *produce(void *argument) {
    struct channel *channel = argument;
    for (uint64_t sequence = 1; sequence <= channel->count; ++sequence) {
        struct message message;
        memset(&message, 0, sizeof(message));
        message.sequence = sequence; message.channel = channel->id;
        for (size_t i = 0; i < sizeof(message.bytes); ++i) message.bytes[i] = (unsigned char)(sequence * 17 + channel->id * 31 + i);
        message.checksum = checksum(&message);
        for (;;) {
            if (atomic_load_explicit(&failed, memory_order_relaxed)) return NULL;
            int result = aegis_queue_push(channel->queue, &message, sizeof(message));
            if (result == 1) break;
            if (result < 0) { atomic_store(&failed, 1); return NULL; }
            channel->full_spins++;
            if (channel->full_spins % 256 == 0) sched_yield();
        }
    }
    return NULL;
}

static void *consume(void *argument) {
    struct channel *channel = argument;
    for (uint64_t sequence = 1; sequence <= channel->count; ++sequence) {
        struct message message;
        int64_t result;
        for (;;) {
            if (atomic_load_explicit(&failed, memory_order_relaxed)) return NULL;
            result = aegis_queue_pop(channel->queue, &message, sizeof(message));
            if (result != 0) break;
            channel->empty_spins++;
            if (channel->empty_spins % 256 == 0) sched_yield();
        }
        if (result != (int64_t)sizeof(message) || message.sequence != sequence ||
            message.channel != channel->id || message.checksum != checksum(&message)) {
            fprintf(stderr, "Ordering/checksum failure channel=%" PRIu64 " sequence=%" PRIu64 "\n", channel->id, sequence);
            atomic_store(&failed, 1); return NULL;
        }
        for (size_t i = 0; i < sizeof(message.bytes); ++i) {
            if (message.bytes[i] != (unsigned char)(sequence * 17 + channel->id * 31 + i)) { atomic_store(&failed, 1); return NULL; }
        }
    }
    return NULL;
}

int main(int argc, char **argv) {
    uint64_t count = 100000;
    if (argc > 2) return 2;
    if (argc == 2) {
        char *end; errno = 0;
        unsigned long long parsed = strtoull(argv[1], &end, 10);
        if (errno != 0 || end == argv[1] || *end != '\0' || parsed == 0 || parsed > 10000000) return 2;
        count = parsed;
    }
    struct channel channels[CHANNELS];
    pthread_t producers[CHANNELS], consumers[CHANNELS];
    memset(channels, 0, sizeof(channels)); atomic_init(&failed, 0);
    for (size_t i = 0; i < CHANNELS; ++i) {
        channels[i].queue = aegis_queue_create(127, sizeof(struct message));
        channels[i].id = i; channels[i].count = count;
        if (channels[i].queue == NULL) { fprintf(stderr, "Allocation failed or atomics not lock-free\n"); return 1; }
        int error = pthread_create(&consumers[i], NULL, consume, &channels[i]);
        if (error == 0) error = pthread_create(&producers[i], NULL, produce, &channels[i]);
        if (error != 0) { fprintf(stderr, "pthread_create: %s\n", strerror(error)); return 1; }
    }
    uint64_t full = 0, empty = 0;
    for (size_t i = 0; i < CHANNELS; ++i) {
        int first = pthread_join(producers[i], NULL), second = pthread_join(consumers[i], NULL);
        if (first != 0 || second != 0) return 1;
        full += channels[i].full_spins; empty += channels[i].empty_spins;
        aegis_queue_destroy(channels[i].queue);
    }
    if (atomic_load(&failed)) return 1;
    printf("{\"channels\":4,\"messages_per_channel\":%" PRIu64 ",\"verified_messages\":%" PRIu64 ",\"full_retries\":%" PRIu64 ",\"empty_retries\":%" PRIu64 "}\n", count, count * CHANNELS, full, empty);
    return 0;
}
