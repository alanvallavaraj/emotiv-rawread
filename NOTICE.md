# Third-party work this repository builds on

Nothing here replaces or republishes the projects below. The patches in
`patches/` apply to a copy of `emotiv-rs` that you obtain yourself, and the
findings documented in the README were reached by reading the open-source
drivers listed here.

## emotiv-rs

<https://github.com/eugenehp/emotiv-rs> - crate `emotiv`, version 0.0.14, MIT
licence, Copyright (c) 2026 Emotiv Rust Contributors.

The patches in `patches/` are diffs against that release. Obtain the crate from
crates.io or from the upstream repository; its licence travels with it.

## emokit

<https://github.com/openyou/emokit> - the original open-source driver for the
Emotiv EPOC. The AES key derivations reimplemented in the decryption patch
(`new_crypto_key`, `epoc_plus_crypto_key`, `crypto_key`) are taken from its
`python/emokit/util.py`.

## CyKit

<https://github.com/CymatiCorp/CyKit> - the EPOC+ byte-pair packet layout and
the microvolt conversion in the decryption patch follow
`Examples/example_epoc_plus.py`. Its key derivation agrees byte for byte with
emokit's, which is what gave confidence in both.
