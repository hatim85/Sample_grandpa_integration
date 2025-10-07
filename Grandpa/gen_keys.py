# gen_keys.py
# Run this once to create keys.json for your validator set.

import json
import os
from base64 import b64encode
from nacl.signing import SigningKey
try:
    from blspy import PrivateKey as BLSPrivateKey
    have_bls = True
except Exception:
    have_bls = False

def gen_ed25519():
    sk = SigningKey.generate()
    pk = sk.verify_key
    return b64encode(sk.encode()).decode(), b64encode(pk.encode()).decode()

def gen_bls():
    if not have_bls:
        return None, None
    sk = BLSPrivateKey.from_seed(os.urandom(32))
    pk = sk.get_g1()
    return sk.serialize().hex(), pk.serialize().hex()

def main():
    validators = []
    # change this number to the validator count you want
    NUM = 5
    for i in range(NUM):
        ed_sk, ed_pk = gen_ed25519()
        bls_sk, bls_pk = gen_bls()
        validators.append({
            "id": i,
            "ed_sk_b64": ed_sk,
            "ed_pk_b64": ed_pk,
            "bls_sk_hex": bls_sk,
            "bls_pk_hex": bls_pk
        })

    with open("keys.json", "w") as f:
        json.dump({"validators": validators, "have_bls": have_bls}, f, indent=2)
    print("Wrote keys.json (have_bls=%s). Keep this file secret for private keys." % have_bls)

if __name__ == "__main__":
    main()
