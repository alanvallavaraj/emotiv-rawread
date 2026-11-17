#!/usr/bin/env python3
"""Offline search for the AES serial of an Emotiv headset.

Takes a capture of raw notifications from the EEG characteristic, reassembles
them into packets, and tries candidate serials until one decrypts to a stream
whose packet counter increments by one. Nothing here talks to the headset, so
hundreds of candidates cost seconds rather than a connection each.

The three key derivations mirror src/raw/decryption.rs exactly.
"""

import sys
from collections import Counter
from Crypto.Cipher import AES

PACKET_LEN = 32


def derive_v1(s: bytes) -> bytes:
    k = bytearray(16)
    k[0], k[1], k[2], k[3] = s[1], 0x00, s[2], s[3]
    k[4], k[5], k[6], k[7] = s[4], s[8], s[9], s[7]
    k[8], k[9], k[10], k[11] = s[10], s[11], s[0], s[6]
    k[12], k[13], k[14], k[15] = s[5], 0x54, 0x10, 0x42
    return bytes(k)


def derive_v2(s: bytes) -> bytes:
    k = bytearray(16)
    k[0], k[1], k[2], k[3] = s[0], s[3], s[7], s[1]
    k[4], k[5], k[6], k[7] = s[5], s[11], s[4], s[9]
    k[8], k[9], k[10], k[11] = s[2], s[6], s[10], s[8]
    k[12], k[13], k[14], k[15] = 0x31, 0x31, 0x35, 0x39
    return bytes(k)


def derive_epocx(s: bytes) -> bytes:
    return bytes(s[:12]) + bytes([0x45, 0x58, 0x31, 0x30])


DERIVATIONS = {"v1": derive_v1, "v2": derive_v2, "epocx": derive_epocx}


def reassemble(notifications, packet_len=PACKET_LEN):
    """Join fragmented notifications into whole packets.

    At the default MTU a 32-byte packet arrives as 20 + 12.
    """
    packets, buf = [], b""
    for n in notifications:
        buf += n
        while len(buf) >= packet_len:
            packets.append(buf[:packet_len])
            buf = buf[packet_len:]
    return packets


def score(packets, key):
    """Fraction of consecutive packets whose counter increments by one.

    A correct key gives a value near 1.0; a wrong one gives ~1/65536.
    """
    cipher = AES.new(key, AES.MODE_ECB)
    counters = []
    for p in packets:
        d = cipher.decrypt(p[:16]) + cipher.decrypt(p[16:32])
        counters.append((d[0] << 8) | d[1])
    if len(counters) < 2:
        return 0.0, counters
    good = sum(
        1 for i in range(len(counters) - 1)
        if (counters[i + 1] - counters[i]) % 65536 == 1
    )
    return good / (len(counters) - 1), counters


def candidates(bases, prefixes, min_len=12):
    """Every prefix+base+padding combination that is long enough to key."""
    out = []
    seen = set()
    for base in bases:
        for pre in prefixes:
            for pad_char in ("", "0", "X"):
                stem = pre + base
                for target in (12, 16):
                    if len(stem) > target and pad_char:
                        continue
                    s = stem if not pad_char else stem.ljust(target, pad_char)
                    for variant in (s, s.upper(), s.lower()):
                        if len(variant) >= min_len and variant not in seen:
                            seen.add(variant)
                            out.append(variant)
    return out


def sweep(packets, serials, verbose=True):
    results = []
    for s in serials:
        b = s.encode("ascii", "ignore")
        if len(b) < 12:
            continue
        for name, fn in DERIVATIONS.items():
            try:
                key = fn(b)
            except IndexError:
                continue
            sc, _ = score(packets, key)
            results.append((sc, s, name))
    results.sort(reverse=True)
    if verbose:
        for sc, s, name in results[:10]:
            print(f"  {sc:7.4f}  {s!r:24} {name}")
    return results


def self_test():
    """Encrypt a synthetic stream with a known serial and recover it."""
    truth = "RIGHTSERIAL2"
    key = derive_v2(truth.encode())
    cipher = AES.new(key, AES.MODE_ECB)
    packets = []
    for i in range(200):
        plain = bytearray(32)
        plain[0] = (i >> 8) & 0xFF
        plain[1] = i & 0xFF
        for j in range(2, 32):
            plain[j] = (i * j) & 0xFF
        packets.append(cipher.encrypt(bytes(plain[:16])) + cipher.encrypt(bytes(plain[16:])))

    hit, _ = score(packets, key)
    wrong, _ = score(packets, derive_v2(b"WRONGSERIAL1"))
    found = sweep(packets, ["WRONGSERIAL1", "NOTITEITHER1", truth, "ALSOWRONG123"], verbose=False)
    best = found[0]
    print(f"self-test: correct key scores {hit:.4f}, wrong key {wrong:.6f}")
    print(f"self-test: sweep picked {best[1]!r} via {best[2]} at {best[0]:.4f}")
    ok = hit == 1.0 and wrong < 0.01 and best[1] == truth
    print("SELF-TEST", "PASSED" if ok else "FAILED")
    return ok



# ── Direct key constructions ─────────────────────────────────────────────────
# The library assumes a serial of 12+ characters. This headset's real serial is
# 8 ("4A2C0290"), so the key cannot come from those derivations at all. These
# build 16-byte keys straight from the serial instead, covering the usual ways
# a short serial is stretched to AES-128.

def direct_keys(serial="4A2C0290"):
    """Every plausible 16-byte key built from a short serial. Returns (name, key)."""
    out = []
    a = serial.encode()                      # ASCII, 8 bytes
    a_lo = serial.lower().encode()
    try:
        h = bytes.fromhex(serial)            # as hex -> 4 bytes
    except ValueError:
        h = b""

    def add(name, k):
        if len(k) == 16:
            out.append((name, bytes(k)))

    for tag, base in (("ascii", a), ("ascii_lower", a_lo)):
        add(f"{tag}x2", base * 2)
        add(f"{tag}x2_rev", base + base[::-1])
        add(f"{tag}_rev_x2", base[::-1] * 2)
        add(f"{tag}_pad0", base + b"\x00" * 8)
        add(f"{tag}_pad0_front", b"\x00" * 8 + base)
        add(f"{tag}_padspace", base + b" " * 8)

    if h:
        add("hexx4", h * 4)
        add("hexx4_rev", h[::-1] * 4)
        add("hex_pad0", h + b"\x00" * 12)
        add("hex_then_ascii", h + a + b"\x00" * 4)
        # EPOC-family constants seen in the library's derivations
        add("hexx3_T10B", h * 3 + bytes([0x54, 0x10, 0x42, 0x00]))
        add("hexx3_1159", h * 3 + bytes([0x31, 0x31, 0x35, 0x39]))
        add("hexx3_EX10", h * 3 + bytes([0x45, 0x58, 0x31, 0x30]))

    # ASCII serial plus each derivation's trailing constant
    add("ascii_T10B", a + a[:4] + bytes([0x54, 0x10, 0x42, 0x00]))
    add("ascii_1159", a + a[:4] + bytes([0x31, 0x31, 0x35, 0x39]))
    add("ascii_EX10", a + a[:4] + bytes([0x45, 0x58, 0x31, 0x30]))
    return out


def sweep_direct(packets, serial="4A2C0290", verbose=True):
    results = []
    for name, key in direct_keys(serial):
        sc, _ = score(packets, key)
        results.append((sc, name, key.hex()))
    results.sort(reverse=True)
    if verbose:
        for sc, name, kh in results[:10]:
            print(f"  {sc:7.4f}  {name:20} key={kh}")
    return results


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        sys.exit(0 if self_test() else 1)

    path = sys.argv[1]
    notifications = [bytes.fromhex(l.strip()) for l in open(path) if l.strip()]
    print(f"{len(notifications)} notifications, lengths: "
          f"{Counter(len(n) for n in notifications).most_common()}")
    packets = reassemble(notifications)
    print(f"reassembled into {len(packets)} packets of {PACKET_LEN} bytes")

    bases = [
        # Cortex's own identifier for this headset, read from queryHeadsets.
        # 17 characters, so it satisfies the 12-char requirement with no padding.
        "EPOCPLUS-4A2C0290", "EPOCPLUS4A2C0290", "EPOCPLUS_4A2C0290",
        "4A2C0290", "4A2C02904A2C0290", "4A2C02904A2C", "0920C2A4",
        "E349649", "4A2C0290FA", "201807260038EF", "UD201807260038EF",
    ]
    prefixes = [
        "", "SN", "EP", "EM", "UD", "HS", "SE",
        "EPOC", "EPOC+", "EPOCPLUS", "EPOCPLUS-", "EMOTIV", "EPOCPLUS0",
    ]
    serials = candidates(bases, prefixes)
    print(f"testing {len(serials)} candidate serials x {len(DERIVATIONS)} derivations "
          f"= {len(serials) * len(DERIVATIONS)} keys\n")
    results = sweep(packets, serials)

    print("\n--- direct 16-byte key constructions ---")
    direct = []
    for s in ("4A2C0290", "EPOCPLUS-4A2C0290", "EPOCPLUS4A2C0290"):
        for sc, name, kh in sweep_direct(packets, s, verbose=False):
            direct.append((sc, f"{s}:{name}", kh))
    direct.sort(reverse=True)
    for sc, name, kh in direct[:8]:
        print(f"  {sc:7.4f}  {name:34} key={kh}")
    results = sorted(results + [(s, n, "direct:" + k[:16]) for s, n, k in direct],
                     reverse=True)
    best = results[0]
    print(f"\nbest: {best[1]!r} via {best[2]} scoring {best[0]:.4f}")
    print("A correct key scores near 1.0; pure chance is 0.000015.")
    if best[0] < 0.5:
        print("VERDICT: no candidate works - the serial is not derivable from these bases.")
    else:
        print("VERDICT: FOUND")
