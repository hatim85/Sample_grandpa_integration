# grandpa_sim.py
# Educational GRANDPA-like finality gadget simulation (Jam-adapted)
# - No external libraries required (pure Python stdlib)
# - Save and run: python grandpa_sim.py

import hashlib
import hmac
import time
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set
  
# ----------------------------- Utilities ----------------------------------
def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def hmac_sign(key: bytes, msg: str) -> str:
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()

# ----------------------------- Blockchain types ---------------------------
@dataclass
class BlockHeader:
    parent_hash: str
    height: int
    state_root: str
    slot: int
    author: int  # validator index
    ticketed: bool  # whether author was the scheduled ticket winner
    def hash(self) -> str:
        payload = f"{self.parent_hash}|{self.height}|{self.state_root}|{self.slot}|{self.author}|{int(self.ticketed)}"
        return sha256_hex(payload)

@dataclass
class Block:
    header: BlockHeader
    body: str = ""  # placeholder for transactions / extrinsics
    audited: bool = True  # audited flag for Jam-style validation

# ----------------------------- Simple keyring -----------------------------
class Keyring:
    def __init__(self):
        self.secrets: Dict[int, bytes] = {}
    def add_validator(self, vid: int):
        # deterministic demo secret (do NOT use deterministic derivation in production)
        seed = f"validator-secret-{vid}".encode()
        self.secrets[vid] = hashlib.sha256(seed).digest()
    def sign(self, vid: int, message: str) -> str:
        return hmac_sign(self.secrets[vid], message)
    def verify(self, vid: int, message: str, signature: str) -> bool:
        expected = hmac_sign(self.secrets[vid], message)
        return hmac.compare_digest(expected, signature)

# ----------------------------- Blockchain ---------------------------------
class SimpleBlockchain:
    def __init__(self, genesis_state_root: str = "GENESIS"):
        self.blocks: Dict[str, Block] = {}
        self.children: Dict[str, List[str]] = {}  # parent_hash -> list of child_hashes
        self.genesis = self._make_genesis(genesis_state_root)
        self.finalized_hash: str = self.genesis.header.hash()
        self.heads: Set[str] = {self.genesis.header.hash()}

    def _make_genesis(self, state_root: str) -> Block:
        header = BlockHeader(parent_hash="0"*64, height=0, state_root=state_root, slot=0, author=-1, ticketed=True)
        b = Block(header=header, body="genesis", audited=True)
        h = header.hash()
        self.blocks[h] = b
        self.children[h] = []
        return b

    def add_block(self, block: Block):
        h = block.header.hash()
        if h in self.blocks:
            return h  # already present
        self.blocks[h] = block
        parent = block.header.parent_hash
        if parent not in self.children:
            self.children[parent] = []
        self.children[parent].append(h)
        # update heads: remove parent if it was a head, add this as head
        if parent in self.heads:
            self.heads.remove(parent)
        self.heads.add(h)
        return h

    def get_block(self, block_hash: str) -> Block:
        return self.blocks[block_hash]

    def chain_from(self, head_hash: str) -> List[str]:
        # return list of block hashes from genesis (inclusive) to head (inclusive)
        chain = []
        cur = head_hash
        while True:
            chain.append(cur)
            cur = self.blocks[cur].header.parent_hash
            if cur == "0"*64:
                break
        chain.reverse()
        return chain

    def count_ticketed_in_chain_since(self, head_hash: str, ancestor_hash: Optional[str]=None) -> int:
        # Count ticketed blocks between ancestor_hash (exclusive) -> head (inclusive).
        if ancestor_hash is None:
            ancestor_hash = self.finalized_hash
        count = 0
        cur = head_hash
        while cur != ancestor_hash and cur != "0"*64:
            block = self.blocks[cur]
            if block.header.ticketed:
                count += 1
            cur = block.header.parent_hash
        return count

    def chain_has_equivocation(self, head_hash: str) -> bool:
        # detect if any (author,slot) pair repeats in the subchain after finalized block.
        seen = {}
        cur = head_hash
        while True:
            if cur == self.finalized_hash or cur == "0"*64:
                break
            block = self.blocks[cur]
            key = (block.header.author, block.header.slot)
            if key in seen and seen[key] != cur:
                # two different blocks from same author at same slot -> equivocation
                return True
            seen[key] = cur
            cur = block.header.parent_hash
        return False

    def chain_contains_invalid(self, head_hash: str) -> bool:
        # if any post-finalized block in chain is not audited => invalid chain
        cur = head_hash
        while True:
            if cur == self.finalized_hash or cur == "0"*64:
                break
            if not self.blocks[cur].audited:
                return True
            cur = self.blocks[cur].header.parent_hash
        return False

# ----------------------------- Vote messages -------------------------------
@dataclass
class Vote:
    round: int
    stage: str  # 'prevote' or 'precommit'
    block_hash: Optional[str]  # None represents nil
    block_height: Optional[int]
    state_root: Optional[str]
    validator: int
    signature: str
    def message_to_sign(self) -> str:
        bh = self.block_hash or "nil"
        bhgt = str(self.block_height) if self.block_height is not None else "nil"
        sr = self.state_root or "nil"
        return f"{self.round}|{self.stage}|{bh}|{bhgt}|{sr}"

# ----------------------------- GRANDPA Node --------------------------------
class GrandpaNode:
    def __init__(self, vid: int, keyring: Keyring, blockchain: SimpleBlockchain, validators: List[int]):
        self.vid = vid
        self.keyring = keyring
        self.chain = blockchain
        self.validators = validators
        self.N = len(validators)
        self.threshold = (2*self.N)//3 + 1  # strict > 2/3 threshold -> at least floor(2N/3)+1
        self.round = 0

    def compute_best_head(self) -> str:
        # Select heads that extend finalized block, are fully audited, and have no equivocation.
        candidates = []
        for h in list(self.chain.heads):
            # ensure h extends finalized
            cur = h
            extends = False
            while cur != "0"*64:
                if cur == self.chain.finalized_hash:
                    extends = True
                    break
                cur = self.chain.get_block(cur).header.parent_hash
            if not extends:
                continue
            if self.chain.chain_contains_invalid(h):
                continue
            if self.chain.chain_has_equivocation(h):
                continue
            candidates.append(h)
        if not candidates:
            return self.chain.finalized_hash
        # tie-breaker: maximize ticketed count since finalized
        best = max(candidates, key=lambda h: self.chain.count_ticketed_in_chain_since(h))
        return best

    def create_prevote(self, head_hash: str) -> Vote:
        blk = self.chain.get_block(head_hash)
        msg = f"{self.round}|prevote|{head_hash}|{blk.header.height}|{blk.header.state_root}"
        sig = self.keyring.sign(self.vid, msg)
        return Vote(round=self.round, stage='prevote', block_hash=head_hash,
                    block_height=blk.header.height, state_root=blk.header.state_root,
                    validator=self.vid, signature=sig)

    def create_precommit(self, target_hash: Optional[str]) -> Vote:
        if target_hash is None:
            msg = f"{self.round}|precommit|nil|nil|nil"
            sig = self.keyring.sign(self.vid, msg)
            return Vote(round=self.round, stage='precommit', block_hash=None,
                        block_height=None, state_root=None, validator=self.vid, signature=sig)
        blk = self.chain.get_block(target_hash)
        msg = f"{self.round}|precommit|{target_hash}|{blk.header.height}|{blk.header.state_root}"
        sig = self.keyring.sign(self.vid, msg)
        return Vote(round=self.round, stage='precommit', block_hash=target_hash,
                    block_height=blk.header.height, state_root=blk.header.state_root,
                    validator=self.vid, signature=sig)

    def verify_vote(self, v: Vote) -> bool:
        return self.keyring.verify(v.validator, v.message_to_sign(), v.signature)

# ----------------------------- Network pool (centralized sim) -------------
class NetworkPool:
    def __init__(self):
        self.messages: List[Vote] = []
    def broadcast(self, v: Vote):
        self.messages.append(v)
    def collect_stage(self, round_no: int, stage: str) -> List[Vote]:
        matched = [m for m in self.messages if m.round == round_no and m.stage == stage]
        random.shuffle(matched)
        return matched

# ----------------------------- Demo scenario ------------------------------
def coordinated_demo(num_validators: int = 5, slots: int = 8, fork_slot: Optional[int] = 4, max_rounds: int = 10):
    print(f"Coordinated GRANDPA simulation: {num_validators} validators\n")
    keyring = Keyring()
    for i in range(num_validators):
        keyring.add_validator(i)
    chain = SimpleBlockchain(genesis_state_root="root0")
    validators = list(range(num_validators))
    nodes = [GrandpaNode(i, keyring, chain, validators) for i in validators]
    pool = NetworkPool()

    # Produce a sequence of blocks (Safrole-like deterministic scheduled author per slot)
    print("Producing blocks (scheduled authors).")
    for slot in range(1, slots+1):
        scheduled_author = slot % num_validators
        head = list(chain.heads)[0]  # naive single-head selection in demo
        parent = head
        height = chain.get_block(parent).header.height + 1
        state_root = f"state_{height}"
        header = BlockHeader(parent_hash=parent, height=height,
                             state_root=state_root, slot=slot,
                             author=scheduled_author, ticketed=True)
        blk = Block(header=header, body=f"slot-{slot}", audited=True)
        h1 = chain.add_block(blk)
        print(f"  Slot {slot}: scheduled author {scheduled_author} produced block {h1[:8]} (height {height})")
        # optionally create a fork at fork_slot by a different author
        if fork_slot is not None and slot == fork_slot:
            rogue_author = (scheduled_author + 1) % num_validators
            header2 = BlockHeader(parent_hash=parent, height=height,
                                  state_root=state_root + "_fork", slot=slot,
                                  author=rogue_author, ticketed=False)
            blk2 = Block(header=header2, body=f"slot-{slot}-fork", audited=True)
            h2 = chain.add_block(blk2)
            print(f"    Fork created at slot {slot} by rogue author {rogue_author}: {h2[:8]} (height {height})")

    print("\nChain heads after production:")
    for h in chain.heads:
        print(" ", h[:8], "height=", chain.get_block(h).header.height, "ticketed=", chain.get_block(h).header.ticketed)

    # Run coordinated GRANDPA rounds
    print("\nRunning GRANDPA rounds...")
    finalized = None
    for r in range(max_rounds):
        print(f"\n--- Round {r} ---")
        # prevote phase: all nodes compute best head and broadcast a prevote
        for node in nodes:
            best = node.compute_best_head()
            pv = node.create_prevote(best)
            pool.broadcast(pv)
            print(f"Node {node.vid} prevoted {pv.block_hash[:8]} (height {pv.block_height})")
        # collect prevotes centrally (simulate gossip converged)
        prevotes = pool.collect_stage(r, 'prevote')
        tallies = {}
        for v in prevotes:
            if not nodes[v.validator].verify_vote(v): 
                continue
            tallies.setdefault(v.block_hash, []).append(v)
        pv_super = None
        for bh, votes in tallies.items():
            if len(votes) >= nodes[0].threshold:
                pv_super = bh
                break
        print("Prevote supermajority for:", pv_super[:8] if pv_super else "None")
        # precommit phase: all nodes precommit according to observed prevote-supermajority
        for node in nodes:
            pc = node.create_precommit(pv_super)
            pool.broadcast(pc)
            print(f"Node {node.vid} precommitted { (pc.block_hash[:8] if pc.block_hash else 'nil') }")
        precommits = pool.collect_stage(r, 'precommit')
        tallies_pc = {}
        for v in precommits:
            if not nodes[v.validator].verify_vote(v):
                continue
            tallies_pc.setdefault(v.block_hash, []).append(v)
        # check precommit supermajority to finalize
        finalized_block = None
        justification = None
        for bh, votes in tallies_pc.items():
            if bh is not None and len(votes) >= nodes[0].threshold:
                finalized_block = bh
                justification = [f"{v.validator}:{v.signature[:16]}" for v in votes]
                break
        if finalized_block:
            chain.finalized_hash = finalized_block
            print(f"\n*** FINALIZED {finalized_block[:8]} height={chain.get_block(finalized_block).header.height} in round {r} ***")
            print("Justification (truncated sigs):", justification)
            finalized = finalized_block
            break
        else:
            print("No finalization this round.")
    if finalized is None:
        print("\nNo finalization reached in allocated rounds.")
    else:
        print("\nFinalized chain from genesis:")
        for h in chain.chain_from(finalized):
            blk = chain.get_block(h)
            print(f"  {h[:8]}  h={blk.header.height} slot={blk.header.slot} author={blk.header.author} ticketed={blk.header.ticketed}")

# ---------- run demo if executed directly ----------
if __name__ == "__main__":
    # change these params to experiment:
    coordinated_demo(num_validators=5, slots=8, fork_slot=4, max_rounds=6)
