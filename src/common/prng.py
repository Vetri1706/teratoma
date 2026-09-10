"""Reproducible unsigned Xorshift128+ seeded with SplitMix64."""
_MASK = (1 << 64) - 1


class Xorshift128Plus:
    def __init__(self, seed: int):
        state = seed & _MASK
        words = []
        for _ in range(2):
            state = (state + 0x9E3779B97F4A7C15) & _MASK
            value = state
            value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
            value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK
            words.append((value ^ (value >> 31)) & _MASK)
        self.s0, self.s1 = words

    @property
    def state(self):
        return self.s0, self.s1

    def next_u64(self):
        left, right = self.s0, self.s1
        self.s0 = right
        left ^= (left << 23) & _MASK
        self.s1 = (left ^ right ^ (left >> 17) ^ (right >> 26)) & _MASK
        return (self.s1 + right) & _MASK

    def randbelow(self, n):
        if not isinstance(n, int) or n <= 0:
            raise ValueError("randbelow requires a positive integer")
        if n <= 1 << 64:
            threshold = ((1 << 64) - n) % n
            while True:
                value = self.next_u64()
                if value >= threshold:
                    return value % n
        bits = n.bit_length()
        while True:
            value = 0
            for _ in range((bits + 63) // 64):
                value = (value << 64) | self.next_u64()
            value &= (1 << bits) - 1
            if value < n:
                return value

    def chance(self, numerator, denominator=1000):
        if not isinstance(numerator, int) or not isinstance(denominator, int):
            raise ValueError("chance arguments must be integers")
        if denominator <= 0 or not 0 <= numerator <= denominator:
            raise ValueError("invalid probability")
        if numerator == 0:
            return False
        if numerator == denominator:
            return True
        return self.randbelow(denominator) < numerator

    def choice(self, sequence):
        if len(sequence) == 0:
            raise ValueError("cannot choose from an empty sequence")
        return sequence[self.randbelow(len(sequence))]
