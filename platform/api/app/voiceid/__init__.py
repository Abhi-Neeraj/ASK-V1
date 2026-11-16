"""Voice characteristics measured directly from audio, with no model and no network.

Three layers, each usable on its own:

* `pitch`: how high or low a voice is (base, low and high pitch), the first thing used to tell speakers apart.
* `signature`: a compact "voice signature" (pitch plus the shape of the voice's spectrum) and the rules for
  comparing two signatures.
* `matching`: choosing who a signature belongs to, or saying it is nobody in particular.

Everything here is pure arithmetic on numbers, so every rule can be tested exactly. Nothing here reads the
database, and nothing here is identity verification: a signature is a coarse, uncalibrated fingerprint, and a
wrong name is always worse than "Unknown speaker".
"""
